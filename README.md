# IPTV 自动更新项目

这个项目使用 GitHub Actions 定时自动更新 IPTV M3U 播放列表。

## 功能特点

- 从多个源下载 M3U 播放列表
- 测试链接有效性，过滤无效链接
- 过滤特定分组（Hong Kong, Taiwan, China, Information）
- 过滤特定域名
- 每天自动更新一次

## 文件说明

- `iptv_updater.py` - 主脚本，负责更新 M3U 文件
- `new.m3u` - 生成的最终 M3U 播放列表
- `.github/workflows/update-iptv.yml` - GitHub Actions 工作流配置

## 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 运行脚本
python iptv_updater.py
```

## 定时任务

脚本会在每天北京时间凌晨 2:00 自动运行（UTC 时间 18:00）。

你也可以在 GitHub 仓库的 Actions 标签页手动触发运行。
