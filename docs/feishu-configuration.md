# 飞书完整webhook地址与代理注入

## 原因

云平台“密钥”绑定可用时，进程可能只能拿到代理占位符。Notion/Gemini把占位符放入固定域名请求的认证头，代理可在指定目的地替换；飞书的完整webhook本身是目的URL，应用必须先获得可解析地址，不能把占位符当URL，也不能通过绕开代理、关闭TLS或日志回显秘密来解决。

## 配置

安全环境设置的“环境变量”页添加 FEISHU_WEBHOOK_DIRECT_URL，值填原飞书机器人的完整HTTPS webhook地址（https://open.feishu.cn/open-apis/bot/v2/hook/...）。不是在“代理密钥”页新增同名项；不在聊天或代码填写。现有FEISHU_WEBHOOK_URL可保留。

feishu_sender_from_env优先直接变量；没有该项时仍接受原来本地/GitHub的完整FEISHU_WEBHOOK_URL，兼容旧配置。若取得的不是合法地址，发送前返回不含秘密值的指导，不尝试猜测、拼接或暴露占位符。代码不读取密钥存储内容，只使用进程注入值。

配置草稿只声明新变量名，不写入地址。保存及发布云开发环境后需重新确认注入；环境发布不代表应用部署。Webhook仍应仅向获准机器人发送，不能提交Git。

## 已准备的两份独立测试报告

当前用户授权10月26/27日两份报告，Notion只读、Gemini分析、上传的地点备份。报告和输入保存在仓库外私有目录 /workspace/private-feishu-validation，未提交。两个文本均小于3KB，各1段。若该目录在新环境中未恢复，应重新只读准备，不假装报告已经存在。

当前发送状态均prepared，尚未请求飞书。报告文本包含地点时间流、活动、占比、空白及异常；原报告跨午夜时间流会按自然日边界裁剪。

配置可用后使用：

```sh
.venv/bin/python -m backend.send_prepared --manifest /workspace/private-feishu-validation/reports/outbox.json --allow-send
```

入口只发送既有文本，不运行迁移、Notion写入、项目累计或Life Calendar。每段发送前持久化sending，成功记录sent；已sent不重发，超时或进程中断为不确定，必须先核对群消息，不盲目重试。发送状态不等于已验证结果内容，用户需在飞书检查显示。

## 验证

后端46项通过，新增代理占位符拒绝、直接变量优先、旧完整URL兼容、超时防重发、业务失败、分段及跨午夜报告裁剪测试。全部使用假发送器。真实飞书发送仍待配置修正，不将平台ready误报为发送成功。
