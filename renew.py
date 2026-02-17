#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DNSHE 免费域名自动续期脚本（多账号，增强错误反馈）
支持将续期结果写入 GitHub Step Summary
"""

import os
import json
import sys
from datetime import datetime

import requests

API_BASE = "https://api005.dnshe.com/index.php?m=domain_hub"
HEADERS_TEMPLATE = {"Content-Type": "application/json"}

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def call_api(endpoint, action, method="GET", api_key=None, api_secret=None, data=None):
    """通用 API 调用函数，返回解析后的 JSON 或错误信息"""
    url = f"{API_BASE}&endpoint={endpoint}&action={action}"
    headers = HEADERS_TEMPLATE.copy()
    if api_key:
        headers["X-API-Key"] = api_key
    if api_secret:
        headers["X-API-Secret"] = api_secret

    log(f"请求 {method} {url}")
    try:
        if method.upper() == "GET":
            resp = requests.get(url, headers=headers, timeout=30)
        else:
            resp = requests.post(url, headers=headers, json=data, timeout=30)

        # 尝试解析 JSON
        try:
            result = resp.json()
        except ValueError:
            # 不是 JSON，可能是 HTML 或其他文本
            content_type = resp.headers.get('Content-Type', '')
            if 'text/html' in content_type:
                return {
                    "success": False,
                    "error": f"服务器返回 HTML 页面 (HTTP {resp.status_code})，可能权限不足或需要登录",
                    "http_status": resp.status_code
                }
            else:
                return {
                    "success": False,
                    "error": f"服务器返回非 JSON 响应 (HTTP {resp.status_code})",
                    "http_status": resp.status_code
                }

        # HTTP 状态码检查
        if resp.status_code != 200:
            error_msg = result.get('message') or result.get('error') or f"HTTP {resp.status_code}"
            # 附加常见状态码说明
            if resp.status_code == 403:
                error_msg += "（域名尚未进入续期窗口，需到期前180天内续期）"
            elif resp.status_code == 401:
                error_msg += "（认证失败，请检查 API Key/Secret）"
            elif resp.status_code == 429:
                error_msg += "（请求过于频繁，请稍后再试）"
            return {"success": False, "error": error_msg, "http_status": resp.status_code}

        return result

    except requests.exceptions.Timeout:
        return {"success": False, "error": "请求超时"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "error": "网络连接失败"}
    except Exception as e:
        return {"success": False, "error": f"请求异常: {str(e)}"}

def renew_subdomain(api_key, api_secret, subdomain_id, full_domain):
    """续期单个子域名，返回状态和消息"""
    log(f"尝试续期 {full_domain} (ID: {subdomain_id})")
    result = call_api(
        endpoint="subdomains",
        action="renew",
        method="POST",
        api_key=api_key,
        api_secret=api_secret,
        data={"subdomain_id": subdomain_id}
    )

    if result.get("success"):
        new_expires = result.get("new_expires_at", "未知")
        log(f"✅ {full_domain} 续期成功，新到期时间: {new_expires}")
        return {
            "status": "success",
            "message": f"续期成功，新到期时间 {new_expires}"
        }
    else:
        error_msg = result.get("message") or result.get("error") or "未知错误"
        # 如果返回了 HTTP 状态码，附加友好说明（已在 call_api 中处理，这里直接取 error_msg）
        log(f"❌ {full_domain} 续期失败: {error_msg}")
        return {
            "status": "failed",
            "message": error_msg
        }

def process_account(account):
    """处理单个账号：列出所有子域名并尝试续期"""
    api_key = account.get("key")
    api_secret = account.get("secret")
    if not api_key or not api_secret:
        log("账号缺少 key 或 secret，跳过")
        return None

    # 获取子域名列表
    list_res = call_api(
        endpoint="subdomains",
        action="list",
        method="GET",
        api_key=api_key,
        api_secret=api_secret
    )
    if not list_res.get("success"):
        error_msg = list_res.get("message") or list_res.get("error") or "列表获取失败"
        log(f"获取子域名列表失败: {error_msg}")
        return {"error": error_msg}

    subdomains = list_res.get("subdomains", [])
    if not subdomains:
        log("该账号下没有子域名")
        return {"message": "无子域名"}

    results = []
    for sub in subdomains:
        sub_id = sub.get("id")
        full_domain = sub.get("full_domain") or f"{sub.get('subdomain')}.{sub.get('rootdomain')}"
        if not sub_id:
            continue
        res = renew_subdomain(api_key, api_secret, sub_id, full_domain)
        results.append({
            "domain": full_domain,
            "result": res
        })
    return {"results": results}

def main():
    accounts_json = os.getenv("ACCOUNTS_JSON")
    if not accounts_json:
        log("错误: 环境变量 ACCOUNTS_JSON 未设置")
        sys.exit(1)

    try:
        accounts = json.loads(accounts_json)
        if not isinstance(accounts, list):
            raise ValueError("ACCOUNTS 必须是 JSON 数组")
    except Exception as e:
        log(f"解析 ACCOUNTS_JSON 失败: {e}")
        sys.exit(1)

    all_results = []
    for idx, acc in enumerate(accounts):
        log(f"===== 处理账号 {idx+1}/{len(accounts)} =====")
        res = process_account(acc)
        all_results.append({
            "account_index": idx,
            "result": res
        })

    # 写入 GitHub Step Summary
    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write("# DNSHE 免费域名续期结果\n\n")
            f.write(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            for idx, acc_res in enumerate(all_results):
                f.write(f"## 账号 {idx+1}\n\n")
                res = acc_res["result"]
                if not res:
                    f.write("账号配置无效，已跳过\n\n")
                elif "error" in res:
                    f.write(f"❌ 处理失败: {res['error']}\n\n")
                elif "message" in res:
                    f.write(f"ℹ️ {res['message']}\n\n")
                else:
                    results = res.get("results", [])
                    if not results:
                        f.write("无子域名\n\n")
                    else:
                        f.write("| 域名 | 状态 | 详细信息 |\n")
                        f.write("|------|------|----------|\n")
                        for r in results:
                            status_icon = "✅" if r["result"]["status"] == "success" else "❌"
                            f.write(f"| {r['domain']} | {status_icon} | {r['result']['message']} |\n")
                        f.write("\n")
            f.write("---\n")
            f.write("> 自动续期任务执行完毕，更多详情请查看工作流日志。\n")
    else:
        log("未找到 GITHUB_STEP_SUMMARY 环境变量，跳过摘要生成")

    log("所有账号处理完毕")

if __name__ == "__main__":
    main()
