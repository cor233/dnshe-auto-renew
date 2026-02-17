#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DNSHE 免费域名自动续期脚本
支持多账号，尝试续期所有子域名，输出 Markdown 摘要到 GITHUB_STEP_SUMMARY
"""

import os
import json
import sys
from datetime import datetime

import requests

API_BASE = "https://api005.dnshe.com/index.php?m=domain_hub"
HEADERS = {"Content-Type": "application/json"}

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def call_api(endpoint, action, method="GET", api_key=None, api_secret=None, data=None):
    url = f"{API_BASE}&endpoint={endpoint}&action={action}"
    headers = HEADERS.copy()
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
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log(f"API 调用失败: {e}")
        return {"success": False, "error": str(e)}

def renew_subdomain(api_key, api_secret, subdomain_id, full_domain):
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
            "message": f"续期成功，新到期时间 {new_expires}",
            "details": result
        }
    else:
        error_msg = result.get("message") or result.get("error") or "未知错误"
        log(f"❌ {full_domain} 续期失败: {error_msg}")
        return {
            "status": "failed",
            "message": error_msg,
            "details": result
        }

def process_account(account):
    api_key = account.get("key")
    api_secret = account.get("secret")
    if not api_key or not api_secret:
        log("账号缺少 key 或 secret，跳过")
        return None

    list_res = call_api(
        endpoint="subdomains",
        action="list",
        method="GET",
        api_key=api_key,
        api_secret=api_secret
    )
    if not list_res.get("success"):
        log(f"获取子域名列表失败: {list_res}")
        return {"error": list_res.get("message", "列表获取失败")}

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
            "id": sub_id,
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

    summary_file = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "w", encoding="utf-8") as f:
            f.write("# DNSHE 免费域名续期结果\n\n")
            f.write(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            for idx, acc_res in enumerate(all_results):
                f.write(f"## 账号 {idx+1}\n\n")
                res = acc_res["result"]
                if "error" in res:
                    f.write(f"❌ 处理失败: {res['error']}\n\n")
                elif "message" in res:
                    f.write(f"ℹ️ {res['message']}\n\n")
                else:
                    results = res.get("results", [])
                    if not results:
                        f.write("无子域名\n\n")
                    else:
                        f.write("| 域名 | 状态 | 信息 |\n")
                        f.write("|------|------|------|\n")
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
