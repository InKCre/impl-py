# 语义内容验收素材

共享 pytest fixture 运行 `generate_assets.py`，用仓库自有数据生成 PNG、WAV、MP4、带字幕的 MKV、PDF、EPUB、ZIP 和通用二进制文件。生成的文件由 Git 忽略。需要单独生成时运行：

```shell
pdm run python tests/assets/semantic-content/generate_assets.py
```

RSS 和 Memos 集成用例各自定义输入媒体类型及预期 resolver。媒体解释用例使用同一组字节验证字幕、PDF 正文和派生文本检索。
