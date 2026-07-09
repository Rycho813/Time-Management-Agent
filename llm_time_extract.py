import os
import sys
import json
import requests
from dotenv import load_dotenv
from notion_read import read_notion_texts
from google import genai
import time

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

TIME_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "event_name": {"type": "string"},
                    "duration_min": {"type": "integer"},
                    "time_type": {
                        "type": "string",
                        "enum": ["effective", "buffer", "ignored"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": ["event_name", "duration_min", "time_type", "evidence"],
            },
        }
    },
    "required": ["items"],
}

def call_gemini_with_retry(client, prompt: str, max_retries: int = 5):
    for attempt in range(1, max_retries + 1):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config={
                    "temperature": 0,
                    "max_output_tokens": 4096,
                    "response_mime_type": "application/json",
                    "response_json_schema": TIME_EXTRACTION_SCHEMA,
                },
            )
        except Exception as exc:
            error_text = str(exc)
            is_retryable = (
                "503" in error_text
                or "UNAVAILABLE" in error_text
                or "429" in error_text
                or "RESOURCE_EXHAUSTED" in error_text
                or "timeout" in error_text.lower()
            )

            if not is_retryable or attempt == max_retries:
                print("Gemini 调用失败")
                print(exc)
                raise

            wait_seconds = 10 * attempt
            print(f"Gemini 临时不可用，第 {attempt} 次失败，{wait_seconds} 秒后重试...")
            time.sleep(wait_seconds)

def extract_time_info(texts: list[str]) -> dict:
    if not texts:
        return {"items": []}

    if not GEMINI_API_KEY:
        raise RuntimeError("缺少 GEMINI_API_KEY，请检查 .env")

    raw_text = "\n".join(texts)

    prompt = f"""
你是一个时间记录抽取器。请从下面的碎碎念中抽取所有带有明确时长的活动。/no_think

分类规则：
1. effective：写代码、项目、阅读、学习、看书、面试准备、简历相关等真正推进目标的活动。
2. buffer：游戏、刷手机。
3. ignored：吃饭、洗澡、通勤、睡觉、购物、家务、必要生活事务、休息、旅游。

输出要求：
1. 只抽取有明确时长的活动；没有明确时长就不要抽取。
2. duration_min 必须换算成分钟，例如 1h=60，2小时=120，半小时=30。
3. time_type 必须严格从 effective、buffer、ignored 三者中选择一个。
4. 不要编造原文没有的活动或时长。
5. evidence 必须填写原文依据。
6. 只输出 JSON，不要输出解释文字。

原始文本：
{raw_text}
""".strip()

    # payload = {                                       #这里是使用ollama里的本地模型
    #     "model": OLLAMA_MODEL,
    #     "messages": [{"role": "user", "content": prompt}],
    #     "stream": False,
    #     "format": TIME_EXTRACTION_SCHEMA,
    #     "options": {"temperature": 0},
    # }

    # response = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120)
    # if response.status_code != 200:
    #     print("Ollama 调用失败")
    #     print("Status code:", response.status_code)
    #     print(response.text)
    #     raise SystemExit(1)

    # content = response.json()["message"]["content"]

    client = genai.Client(api_key=GEMINI_API_KEY)           #这里是使用google的gemini在线模型

    try:  
        response = call_gemini_with_retry(client, prompt)
    except Exception as exc:  
        print("Gemini 调用失败")  
        print(exc)  
        raise SystemExit(1)  

    content = response.text  

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        print("模型返回内容不是合法 JSON：")
        print(content)
        raise SystemExit(1)

def main() -> None:
    if len(sys.argv) < 2:
        print("用法：python llm_time_extract.py 2026-07-02")
        raise SystemExit(1)

    target_date = sys.argv[1]
    texts = read_notion_texts(target_date)
    result = extract_time_info(texts)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()