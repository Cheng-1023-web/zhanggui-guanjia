# 模板三：JSON 结构化修复（校验失败后的重试模板）

你上一次的输出未能通过 JSON Schema 校验。请严格只输出一个符合 schema 的 JSON 对象，
不要任何解释、不要 markdown 围栏。

## 上一轮输出
{last_output}

## 校验错误
{validation_errors}

## 目标 JSON Schema
{schema}

## 修复提示
- 缺少必填字段：从用户消息与 Observation 中提取，无法提取则用合理默认值并说明
- 类型错误：字符串加引号、数字不加引号
- 多余字段：只保留 schema 中定义的字段
