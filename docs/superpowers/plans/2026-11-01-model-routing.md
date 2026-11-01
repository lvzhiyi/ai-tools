# Model Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 本地优先，可选云端兜底，保持现有调用兼容。
**Architecture:** 在 llm.py 集中路由；[lmstudio.cloud] 默认关闭；质检和环境检查感知云端配置。
**Tech Stack:** Python 3.11+、urllib、jsonschema、unittest。
**Spec:** docs/superpowers/specs/2026-11-01-model-routing-design.md

## Global Constraints

- 云端默认关闭；密钥只从环境变量读取。
- 保留现有 `[lmstudio]` 与 `chat` 接口；每端最多一次请求。
- 云端看图需要独立配置 `vision_model`。
- 不触发真实云端请求；不修改用户已有 CLAUDE.md。

## Review Focus

- 未配置或配置不完整的云端不影响正常本地调用。
- 本地看图模型覆盖不能泄漏到云端模型选择。
- 合法 JSON 但 schema 不合格时也要兜底。
- HTTP 错误正文和密钥不出现在错误或日志中。
- 本地关闭时看图质检仍可走云端；取消不触发兜底。

### Task 1: 路由和校验

**Files:** studio/llm.py, studio/plan.py, studio/vision.py, pyproject.toml, uv.lock, tests/test_llm.py
**Interfaces:** 保留 chat；新增 cloud_status(cfg, *, vision=False) -> tuple[bool, str]；available(cfg, *, vision=False) -> bool。

- [x] 写 unittest：本地成功不调用云端；网络、HTTP、超时、畸形响应、空输出、schema 失败后兜底；云端关闭或缺密钥；两端失败；图片用 vision_model；云端不发 ttl；取消不兜底。
- [x] 运行 `.venv/bin/python -m unittest discover -s tests -v`，确认新增行为失败。
- [x] 实现路由与安全错误；添加 jsonschema 依赖和严格 schema。
- [x] 同命令验证通过。

### Task 2: 调用集成与交付

**Files:** studio/qa.py, studio/checks.py, studio/cli.py, config.toml, README.md, docs/roadmap.md, docs/learning-log.md, tests/test_routing_integration.py
**Interfaces:** 消费 Task 1 的 available/cloud_status；网页继续经 CLI 调用。

- [x] 写测试覆盖 Inspector 在本地关闭时云端看图可用/不可用，以及环境检查配置状态。
- [x] 运行测试确认失败，再接入路由状态与配置。
- [x] 更新示例、使用说明、路线图、学习日志；未配置真实云端，明确此验证限制。
- [x] 全量 unittest、compileall、git diff --check；审查改动后提交。
- [x] 按 CLAUDE.md 写跃迁工作台日志，无法定位写入目标则追加项目待同步清单。


## 执行记录

- 在当前工作区实现，交付分支 codex/model-routing，未新建 worktree；保留用户已有未跟踪 CLAUDE.md，不纳入提交。
- 基线无测试；新增路由测试先失败，核心实现后通过，再补齐集成和协议异常测试。
- 独立只读审查发现畸形 URL 和 HTTPException 边界，已通过失败测试复现并修复；最终全量验证见学习日志。
- 新依赖下载使用 /tmp 缓存；uv.lock 同步。云端保持关闭，未进行付费请求。
