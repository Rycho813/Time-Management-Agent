import os
import sys
import json
import requests
from dotenv import load_dotenv
from notion_read import read_notion_texts

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")


TIME_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_name": {
                        "type": "string"
                    },
                    "duration_min": {
                        "type": "integer"
                    },
                    "time_type": {
                        "type": "string",
                        "enum": ["effective", "ineffective", "neutral"]
                    },
                    "evidence": {
                        "type": "string"
                    }
                },
                "required": [
                    "event_name",
                    "duration_min",
                    "time_type",
                    "evidence"
                ]
            }
        }
    },
    "required": ["items"]
}


def extract_time_info(texts: list[str]) -> dict:
    if not texts:
        return {"items": []}

    raw_text = "\n".join(texts)

    prompt = f"""
你是一个时间记录抽取器。请从下面的碎碎念中抽取所有带有明确时长的事件。/no_think

分类规则：
1. effective：写代码，看书，有效学习、项目开发、技术阅读、课程学习、面试准备。
2. ineffective：游戏，手机、无目的娱乐、拖延、纯浪费时间。
3. neutral：旅游，吃饭、洗澡、通勤、睡觉、必要生活事务。

输出要求：
1. 只抽取有明确时长的事件。
2. duration_min 必须换算成分钟，例如 1h=60，2小时=120，半小时=30。
3. 不要编造原文没有的事件或时长。
4. evidence 必须填写原文依据。
5. 只输出 JSON，不要输出解释文字。

原始文本：
{raw_text}
""".strip()

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        "stream": False,
        "format": TIME_EXTRACTION_SCHEMA,
        "options": {
            "temperature": 0
        }
    }

    response = requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json=payload,
        timeout=120,
    )

    if response.status_code != 200:
        print("Ollama 调用失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    content = response.json()["message"]["content"]

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        print("模型返回内容不是合法 JSON：")
        print(content)
        raise SystemExit(1)


def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python ollama_extract.py 2026-07-02")
        raise SystemExit(1)

    target_date = sys.argv[1]

    texts = read_notion_texts(target_date)
    result = extract_time_info(texts)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()