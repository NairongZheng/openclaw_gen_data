"""主生成脚本 - 生成 + 转换一体化"""
import argparse
import logging
import sys
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import load_config
from src.intent_loader import load_intents
from src.openclaw_wrapper import OpenClawWrapper
from src.llm_client import LLMClient
from src.converter import DataConverter
from src.utils import setup_logging, save_json

logger = logging.getLogger(__name__)


def check_agents_exist(num_workers: int) -> bool:
    """检测所需数量的 agents 是否存在

    Args:
        num_workers: 需要的 worker 数量

    Returns:
        True 如果所有 agents 都存在，否则 False
    """
    result = subprocess.run(
        ["openclaw", "agents", "list"],
        capture_output=True,
        text=True
    )

    missing = []
    for i in range(1, num_workers + 1):
        agent_name = f"gendata-worker-{i}"
        if agent_name not in result.stdout:
            missing.append(agent_name)

    if missing:
        logger.error(f"缺少 {len(missing)} 个 agents: {', '.join(missing)}")
        logger.error(f"请先运行: python scripts/init_agents.py --num-agents {num_workers}")
        return False

    return True


def process_intent(intent_data: Dict[str, Any], agent_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """处理单个 intent"""
    intent_id = intent_data.get("id", "unknown")
    logger.info(f"[{agent_name}] 开始处理 intent: {intent_id}")

    try:
        openclaw = OpenClawWrapper(agent_name)
        llm = LLMClient(
            base_url=config["llm"]["base_url"],
            api_key=config["llm"]["api_key"],
            model=config["llm"]["model"],
            temperature=config["llm"]["temperature"]
        )
        converter = DataConverter()

        conversation_history = []
        max_turns = config["generation"]["max_turns"]

        # 主循环
        for turn in range(max_turns):
            logger.info(f"[{agent_name}] Turn {turn + 1}/{max_turns}")

            llm_result = llm.generate_next_query(
                intent=intent_data["natural_language_intent"],
                persona=intent_data.get("metadata", {}).get("persona", {}),
                conversation_history=conversation_history
            )

            if llm_result.get("completed", False):
                logger.info(f"[{agent_name}] 任务完成: {llm_result.get('reason', '')}")
                break

            query = llm_result.get("query", "")
            if not query:
                logger.warning(f"[{agent_name}] LLM 未生成 query")
                break

            logger.info(f"[{agent_name}] Query: {query[:100]}...")

            response = openclaw.send_message(query, timeout=config["generation"]["timeout"])

            assistant_text = ""
            if response.get("result", {}).get("payloads"):
                for payload in response["result"]["payloads"]:
                    if payload.get("type") == "text":
                        assistant_text += payload.get("text", "")

            conversation_history.append({"role": "user", "content": query})
            conversation_history.append({"role": "assistant", "content": assistant_text})

        session_file = openclaw.get_session_file(openclaw.current_session_id)
        output_file = f"{config['paths']['middle_format_dir']}/intent_{intent_id}.json"
        converter.convert_session_to_middle_format(session_file, intent_data, output_file)

        # 处理完后清空 session
        openclaw.clear_session()

        logger.info(f"[{agent_name}] ✓ Intent {intent_id} 处理完成")
        return {"intent_id": intent_id, "status": "success", "output_file": output_file}

    except Exception as e:
        logger.error(f"[{agent_name}] ✗ Intent {intent_id} 失败: {e}")
        return {"intent_id": intent_id, "status": "failed", "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description="OpenClaw 数据生成")
    parser.add_argument("--config", default="config/config.yaml", help="配置文件")
    parser.add_argument("--limit", type=int, help="限制处理数量")
    parser.add_argument("--concurrent", type=int, help="并发数")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config["paths"]["logs_dir"])

    logger.info("=" * 60)
    logger.info("OpenClaw 数据生成开始")
    logger.info("=" * 60)

    # 检测所需的 agents 是否存在
    num_workers = args.concurrent or config["openclaw"]["num_workers"]
    if not check_agents_exist(num_workers):
        sys.exit(1)

    intents = load_intents(config["paths"]["intents_file"])
    logger.info(f"加载 {len(intents)} 个 intents")

    if args.limit:
        intents = intents[:args.limit]

    logger.info(f"并发数: {num_workers}")

    results = []
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = []
        for i, intent in enumerate(intents):
            agent_name = f"gendata-worker-{(i % num_workers) + 1}"
            future = executor.submit(process_intent, intent, agent_name, config)
            futures.append(future)

        for future in as_completed(futures):
            results.append(future.result())
            logger.info(f"进度: {len(results)}/{len(intents)}")

    success = sum(1 for r in results if r["status"] == "success")
    logger.info(f"完成: 成功 {success}, 失败 {len(results) - success}")

    save_json({"total": len(results), "success": success, "results": results}, 
              f"{config['paths']['output_dir']}/summary.json")


if __name__ == "__main__":
    main()
