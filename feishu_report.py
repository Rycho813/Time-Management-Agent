# 只负责发飞书
import os
import requests
from dotenv import load_dotenv

load_dotenv()

FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL")

def send_feishu_text(text: str) -> None:
    if not FEISHU_WEBHOOK_URL:
        print("未配置 FEISHU_WEBHOOK_URL，跳过飞书发送")
        return

    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }

    response = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=20)
    if response.status_code != 200:
        print("飞书发送失败")
        print("Status code:", response.status_code)
        print(response.text)
        raise SystemExit(1)

    print("飞书发送成功")
