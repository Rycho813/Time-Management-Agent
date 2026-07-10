import os
import sys
import json
import requests
from dotenv import load_dotenv
from notion_read import read_notion_texts
import time

load_dotenv()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
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
                    "project_name": {"type": "string"},
                    "project_status": {
                        "type": "string",
                        "enum": ["open", "closed", "none"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": [
                    "event_name",
                    "duration_min",
                    "time_type",
                    "project_name",
                    "project_status",
                    "evidence",
                ],
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

def call_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": TIME_EXTRACTION_SCHEMA,
        "options": {"temperature": 0},
    }

    try:
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"无法连接 Ollama：{exc}") from exc

    if response.status_code != 200:
        print("Ollama 调用失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    try:
        return response.json()["message"]["content"]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Ollama 返回结果格式异常") from exc


def call_gemini(prompt: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError("当前选择 Gemini，但缺少 GEMINI_API_KEY")

    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError(
            "缺少 google-genai，请执行：python -m pip install google-genai"
        ) from exc

    client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        response = call_gemini_with_retry(client, prompt)
    except Exception as exc:
        print("Gemini 调用失败")
        print(exc)
        raise SystemExit(1)

    if not response.text:
        raise RuntimeError("Gemini 返回内容为空")

    return response.text


def call_llm(prompt: str) -> str:
    print(f"当前 LLM Provider：{LLM_PROVIDER}")

    if LLM_PROVIDER == "ollama":
        print(f"当前使用模型：{OLLAMA_MODEL}")
        return call_ollama(prompt)

    if LLM_PROVIDER == "gemini":
        print(f"当前使用模型：{GEMINI_MODEL}")
        return call_gemini(prompt)

    raise RuntimeError(
        f"不支持的 LLM_PROVIDER：{LLM_PROVIDER}；"
        "目前只支持 ollama 或 gemini"
    )

def extract_time_info(
    texts: list[str],
    existing_project_names: list[str] | None = None,
) -> dict:
    if not texts:
        return {"items": []}

    raw_text = "\n".join(texts)
    existing_projects_text = json.dumps(
        existing_project_names or [],
        ensure_ascii=False,
    )

    prompt = f"""
你是一个时间记录抽取器。请从下面的碎碎念中抽取所有带有明确时长的活动。/no_think

分类规则：
1. effective：写代码、项目、阅读、学习、看书、面试准备、简历相关等真正推进目标的活动。
2. buffer：游戏、刷手机、吃饭、洗澡、通勤、睡觉、购物、家务、必要生活事务、休息、旅游。
3. ignored：所有其他活动。

项目规则：
1. project_name 填写活动所属的长期项目名称；不属于明确项目时填写空字符串。
2. 优先从“已有项目名称”中选择完全一致的名称，不要擅自改写已有项目名称。
3. event_name 只填写当天具体做的事项，不要重复 project_name。
4. 有项目且项目仍在进行时，project_status 填 open。
5. 只有原文明确表示“整个项目已经完成、结项或全部结束”时，project_status 才填 closed。
6. 仅仅完成某个功能、模块、步骤或当天任务，不代表整个项目完成，仍填 open。
7. 没有项目名称时，project_status 必须填 none。

输出要求：
1. 只抽取有明确时长的活动；没有明确时长就不要抽取。
2. duration_min 必须换算成分钟，例如 1h=60，2小时=120，半小时=30。
3. time_type 必须严格从 effective、buffer、ignored 三者中选择一个。
4. 不要编造原文没有的活动、项目、状态或时长。
5. evidence 必须填写原文依据。
6. 只输出 JSON，不要输出解释文字。

已有项目名称：
{existing_projects_text}

原始文本：
{raw_text}
""".strip()

    content = call_llm(prompt)

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