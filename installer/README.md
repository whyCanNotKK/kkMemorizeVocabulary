# 安装包配置

`kkMemorizeVocabulary.iss` 由根目录 `build.ps1` 调用，版本读取 `pyproject.toml`。

`ChineseSimplified.isl` 为 Inno Setup 6.7.3 随源代码提供的社区简体中文翻译，保留原作者信息。来源：
https://github.com/jrsoftware/issrc/blob/is-6_7_3/Files/Languages/Unofficial/ChineseSimplified.isl

编译器来自 Inno Setup 官方固定版本发布，SHA256 取自该发布的 GitHub asset digest；下载与便携解包逻辑见 `scripts/prepare-inno.ps1`。便携模式说明：https://jrsoftware.org/ishelp/topic_technotes.htm

目标程序使用固定 AppId、当前用户安装和独立用户数据目录。不要增加会删除用户数据的卸载规则。
