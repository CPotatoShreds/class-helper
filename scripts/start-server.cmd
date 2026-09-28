@echo off
rem class-helper 服务端启动脚本（含本机网络环境所需的全部环境变量）
rem - HF_ENDPOINT: HuggingFace 镜像（国内直连下载 ASR 模型）
rem - NO_PROXY=* : 绕过系统代理（残留的死代理会让所有 python 网络请求挂死）
cd /d %~dp0..
set HF_ENDPOINT=https://hf-mirror.com
set NO_PROXY=*
set no_proxy=*
uv run class-helper serve
