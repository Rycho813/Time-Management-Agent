"""Explicit send-only entrypoint. No Notion, model, migration or statistic writes."""
import argparse
from pathlib import Path
from .adapters import Transport,feishu_sender_from_env
from .report_delivery import deliver

def main():
    parser=argparse.ArgumentParser(description='发送用户已审阅的本地独立报告；不生成新分析')
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--allow-send',action='store_true',help='显式授权外部消息发送')
    args=parser.parse_args()
    if not args.allow_send:parser.error('必须显式提供--allow-send')
    try:
        sender=feishu_sender_from_env(Transport(allow_network=True))
    except ValueError as exc:
        print(str(exc));return 2
    try:
        result=deliver(args.manifest,sender)
        for entry in result:print(f'{entry["date"]} 第{entry["part"]}段：{entry["status"]}')
        return 0 if all(e['status']=='sent' for e in result) else 1
    except ValueError as exc:
        print(str(exc));return 2
if __name__=='__main__':raise SystemExit(main())
