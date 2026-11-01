# Model Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 前端三档选择本地、云端、自动兜底，并实际控制新任务。
**Architecture:** 设置独立持久化；提交任务快照为 CLI --llm-mode；llm.py 按模式调用。
**Tech Stack:** Python/Starlette、React/TypeScript，现有依赖。
**Spec:** docs/superpowers/specs/2026-11-01-model-selector-design.md

## Global Constraints

- 不修改/提交用户已修改的 config.toml 和现有未跟踪文件。
- 不发真实云端请求，不回显误填在 api_key_env 中的密钥。
- 模式适用于新任务，排队/运行中任务不变；未传 CLI 参数保留原行为。

## Review Focus

- 仅本地失败时无云端调用；仅云端不探测本地。
- 仅云端失败不回退；自动维持原兜底。
- 保存无效模式不能覆盖已有模式；服务重启后恢复选择。
- 队列任务记录提交时模式；所有提交路径经过 manager.submit。
- 云端状态与配置接口不暴露误填密钥。

### Task 1: 后端与路由

**Files:** studio/llm.py, studio/cli.py, studio/web/model_routing.py, studio/web/jobs.py, studio/web/app.py, tests/test_model_selector.py
**Interfaces:** CLI --llm-mode local|cloud|auto；GET/PUT /api/model-routing {mode}；设置和状态函数供 API、队列使用。

- [x] 先写失败测试：三档路由、可用性、配置状态脱敏、设置持久化、队列 argv 快照与 API。
- [x] 运行 unittest 观察失败后实现最小改动。
- [x] 全量 unittest 通过。

### Task 2: 前端与交付

**Files:** web/src/components/ModelSelector.tsx, web/src/main.tsx, web/src/api.ts, web/src/styles.css, web/src/pages/Scripts.tsx, web/src/pages/Projects.tsx, studio/web/static/*, README.md, docs/learning-log.md
**Interfaces:** 使用 /api/model-routing 显示当前模式/配置状态，保存后再显示成功。

- [x] 实现全局选择器；保存、失败、未就绪状态；更新剧本文案。
- [x] 安装锁定的前端开发依赖，typecheck 和构建。
- [x] 目视检查页面与切换效果，独立审查，修正发现。
- [x] 全量测试、compileall、diff check；更新文档并仅提交本次变更。


## 执行记录

- 后端先观察9项新增模式/设置测试失败，再实现；与原有18项合计27项通过。
- 全局选择器与任务提交共享状态；独立审查发现重画图旁路、保存结果未知仍可提交两点，均已修正并复核通过。
- 浏览器验证切换auto、刷新保留，以及模拟服务端已保存但响应失败时拆分镜按钮禁用；重新读取恢复实际auto模式和可提交状态。
- 测试预览使用 /tmp 设置与任务目录，不改变真实网页选择，不提交模型任务。
- TypeScript 6 需要 CSS 导入声明，补 env.d.ts；npm 依赖锁定于 web/package-lock.json。
- 交付保留用户 config.toml 和三个未跟踪文件，不纳入此次提交。
