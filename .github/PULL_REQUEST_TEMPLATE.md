## 改动说明 (Summary)

简要说明本次 Pull Request 解决的问题或引入的新特性。

## 改动类型 (Change Type)

- [ ] 🐛 Bug 修复 (Bug Fix)
- [ ] ✨ 新功能 (New Feature)
- [ ] ⚡️ 性能优化 (Performance Optimization)
- [ ] 📝 文档更新 (Documentation)
- [ ] 🔧 工具链/配置/依赖项更新 (Chore / CI)

## 关联 Issue (Related Issues)

Closes #

## 验证与测试 (Verification & Testing)

请勾选已执行并通过的本地校验：

- [ ] 后端规范检查：`uv run ruff check .` 通过（0 error）
- [ ] 后端静态类型检查：`uv run pyright` 通过（0 error, 0 warning）
- [ ] 后端单元测试：`uv run pytest` 全量通过
- [ ] 前端类型与规范检查（若修改了 `web/`）：`bun run type-check && bun run lint`
- [ ] 实际网络请求与行为已验证，未引入未预期的破坏性变更
