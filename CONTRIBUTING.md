# 贡献指南

感谢您考虑为合工大信息监控系统做出贡献！

## 行为准则

参与本项目即表示您同意遵守我们的 [行为准则](CODE_OF_CONDUCT.md)。

## 如何贡献

### 报告Bug

请使用 [Bug Report 模板](.github/ISSUE_TEMPLATE/bug_report.md) 创建 Issue。

**在报告Bug之前，请检查**：
- 搜索现有的 Issues，确保没有重复
- 确认您使用的是最新版本
- 提供可重现的步骤

### 建议新功能

请使用 [Feature Request 模板](.github/ISSUE_TEMPLATE/feature_request.md) 创建 Issue。

### 提交代码

1. **Fork 本仓库**
   ```bash
   # 在 GitHub 上点击 Fork 按钮
   ```

2. **创建您的特性分支**
   ```bash
   git checkout -b feature/AmazingFeature
   ```

3. **进行更改**
   - 遵循现有的代码风格
   - 添加注释说明复杂的逻辑
   - 更新文档（如果需要）

4. **提交您的更改**
   ```bash
   git commit -m 'Add some AmazingFeature'
   ```
   
   **提交信息规范**：
   - 使用祈使句（"Add" 而不是 "Added"）
   - 简洁明了
   - 首字母大写

5. **推送到分支**
   ```bash
   git push origin feature/AmazingFeature
   ```

6. **打开 Pull Request**
   - 使用 [Pull Request 模板](.github/PULL_REQUEST_TEMPLATE.md)
   - 描述您的更改
   - 链接相关的 Issues

## 代码规范

### Python 代码规范

- 遵循 [PEP 8](https://pep8.org/)
- 使用 4 个空格缩进
- 添加类型提示（推荐）
- 编写文档字符串（docstrings）
- 保持函数简洁（< 50 行）

### 命名约定

- **变量/函数**：`snake_case`
- **类名**：`PascalCase`
- **常量**：`UPPER_SNAKE_CASE`
- **私有成员**：前缀 `_`（单下划线）或 `__`（双下划线）

### 注释

- 使用英文或中文，保持一致性
- 注释解释"为什么"，而不是"是什么"
- 复杂逻辑必须添加注释

### 示例

```python
def process_message(message: str, filter_garbage: bool = True) -> dict:
    """
    处理消息，可选过滤垃圾信息
    
    Args:
        message: 原始消息文本
        filter_garbage: 是否过滤垃圾信息
        
    Returns:
        处理后的消息字典
    """
    # 去除首尾空白
    message = message.strip()
    
    # 如果启用垃圾过滤，检查并过滤
    if filter_garbage:
        # 使用配置中的关键词列表
        if _is_garbage(message):
            return {'filtered': True}
    
    # 提取关键信息
    result = _extract_info(message)
    return result


def _is_garbage(text: str) -> bool:
    """内部函数：判断是否为垃圾信息"""
    # 实现细节...
    pass
```

## 测试

- 为新功能添加测试
- 确保现有测试通过
- 使用 `pytest` 运行测试
  ```bash
  pytest tests/
  ```

## 文档

- 更新 README.md（如果需要）
- 更新 API 文档（如果需要）
- 添加使用示例（如果需要）

## 提交前检查清单

- [ ] 代码遵循项目规范
- [ ] 添加了必要的测试
- [ ] 所有测试通过
- [ ] 更新了相关文档
- [ ] 提交了有意义的提交信息
- [ ] 没有合并冲突

## 社区

- 尊重其他贡献者
- 接受建设性的批评
- 关注项目的最佳利益

## 问题？

如果您有任何问题，请：
- 查看现有 Issues
- 阅读文档
- 联系维护者：2240678683@qq.com

---

再次感谢您的贡献！🎉
