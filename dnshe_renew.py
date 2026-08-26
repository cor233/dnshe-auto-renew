import os
import sys
import json
import time
import requests
from datetime import datetime
from urllib.parse import urlencode

API_BASE = "https://api005.dnshe.com/index.php"


def mask_key(key):
    if not key or len(key) <= 8:
        return "***"
    return key[:6] + "..." + key[-4:]


class DNSHEClient:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.headers = {
            "X-API-Key": api_key,
            "X-API-Secret": api_secret,
            "Content-Type": "application/json",
            "User-Agent": "DNSHE-AutoRenew/1.0"
        }

    def request(self, endpoint, action, method="GET", data=None, params=None):
        url = f"{API_BASE}?m=domain_hub&endpoint={endpoint}&action={action}"
        if params:
            url += "&" + urlencode(params)
        try:
            if method == "GET":
                resp = requests.get(url, headers=self.headers, timeout=30)
            else:
                resp = requests.post(url, headers=self.headers, json=data, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            return {"success": False, "error_code": "network_error", "message": str(e)}

    def list_subdomains(self, page=1, per_page=500):
        return self.request("subdomains", "list", params={
            "page": page,
            "per_page": per_page,
            "include_total": "1",
            "sort_by": "expires_at",
            "sort_dir": "asc"
        })

    def renew_subdomain(self, subdomain_id):
        return self.request("subdomains", "renew", method="POST", data={
            "subdomain_id": subdomain_id
        })

    def get_quota(self):
        return self.request("quota", "", method="GET")


def gh_output(name, value):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"{name}={value}\n")


def run_renewal(accounts_json, renew_days_before=30, dry_run=False):
    try:
        accounts = json.loads(accounts_json)
    except json.JSONDecodeError as e:
        print(f"❌ 账号配置 JSON 解析失败: {e}")
        sys.exit(1)

    if not isinstance(accounts, list):
        print("❌ 账号配置必须是 JSON 数组")
        sys.exit(1)

    summary = {
        "total_accounts": len(accounts),
        "total_domains": 0,
        "renewed": [],
        "skipped": [],
        "failed": [],
        "not_yet_available": [],
        "quota_warnings": []
    }

    now = datetime.now()
    exit_code = 0

    for idx, acc in enumerate(accounts, 1):
        api_key = acc.get("key", "")
        api_secret = acc.get("secret", "")
        account_label = mask_key(api_key)

        print(f"\n{'='*60}")
        print(f"📋 账号 #{idx}  ({account_label})")
        print(f"{'='*60}")

        if not api_key or not api_secret:
            print(f"⚠️  缺少 key 或 secret，跳过")
            summary["failed"].append({"account": account_label, "reason": "缺少认证信息"})
            exit_code = 1
            continue

        client = DNSHEClient(api_key, api_secret)

        quota_res = client.get_quota()
        if quota_res.get("success"):
            q = quota_res.get("quota", {})
            available = q.get("available", 0)
            print(f"💳 配额: 已用 {q.get('used', 0)} / 总额 {q.get('total', 0)} (可用 {available})")
            if available <= 0:
                print(f"⚠️  配额已用完，跳过此账号")
                summary["quota_warnings"].append({"account": account_label, "available": available})
                continue
        else:
            print(f"⚠️  配额查询失败: {quota_res.get('message', '未知错误')}")

        all_subdomains = []
        page = 1
        while True:
            result = client.list_subdomains(page=page, per_page=500)
            if not result.get("success"):
                err_msg = result.get("message", result.get("error", "未知错误"))
                print(f"❌ 获取子域名列表失败: {err_msg}")
                summary["failed"].append({"account": account_label, "reason": f"获取列表失败: {err_msg}"})
                exit_code = 1
                break

            subdomains = result.get("subdomains", [])
            all_subdomains.extend(subdomains)

            pagination = result.get("pagination", {})
            if not pagination.get("has_more", False):
                break
            page += 1
            time.sleep(0.3)

        if not all_subdomains:
            print(f"ℹ️  该账号下没有子域名")
            continue

        print(f"🔍 共发现 {len(all_subdomains)} 个子域名")
        summary["total_domains"] += len(all_subdomains)

        for domain in all_subdomains:
            sid = domain.get("id")
            full_domain = domain.get("full_domain", "unknown")
            status = domain.get("status", "unknown")
            expires_at_str = domain.get("expires_at")
            never_expires = domain.get("never_expires", 0)

            prefix = f"  • {full_domain:<40}"

            if never_expires:
                print(f"{prefix} ➜ 永久有效，跳过")
                summary["skipped"].append({"account": account_label, "domain": full_domain, "reason": "永久有效"})
                continue

            days_until_expire = None
            if expires_at_str:
                try:
                    expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
                    days_until_expire = (expires_at - now).days
                except ValueError:
                    pass

            should_renew = False
            reason = ""
            if status in ("suspended", "expired"):
                should_renew = True
                reason = f"状态={status}"
            elif status == "active" and days_until_expire is not None and days_until_expire <= renew_days_before:
                should_renew = True
                reason = f"剩余{days_until_expire}天"

            if not should_renew:
                status_str = f"剩余{days_until_expire}天" if days_until_expire is not None else "无过期时间"
                print(f"{prefix} ➜ 无需续期 ({status_str})")
                summary["skipped"].append({"account": account_label, "domain": full_domain, "reason": "无需续期"})
                continue

            print(f"{prefix} ➜ {reason}，{'[模拟] ' if dry_run else ''}续期中...", end=" ")

            if dry_run:
                print("✅ 模拟成功")
                summary["renewed"].append({"account": account_label, "domain": full_domain, "mode": "dry_run"})
                continue

            renew_result = client.renew_subdomain(sid)

            if renew_result.get("success"):
                new_expires = renew_result.get("new_expires_at", "unknown")
                charged = renew_result.get("charged_amount", 0)
                print(f"✅ 成功! 新过期: {new_expires}, 扣费: {charged}")
                summary["renewed"].append({
                    "account": account_label,
                    "domain": full_domain,
                    "new_expires_at": new_expires,
                    "charged": charged
                })
            else:
                error_code = renew_result.get("error_code", "")
                err_msg = renew_result.get("message", renew_result.get("error", "未知错误"))

                if error_code == "renewal_not_yet_available":
                    print(f"⏳ 未达续期窗口")
                    summary["not_yet_available"].append({
                        "account": account_label, "domain": full_domain, "message": err_msg
                    })
                elif error_code == "quota_exceeded":
                    print(f"❌ 配额不足")
                    summary["failed"].append({"account": account_label, "domain": full_domain, "error_code": error_code, "message": err_msg})
                    exit_code = 1
                else:
                    print(f"❌ 失败 [{error_code}]: {err_msg}")
                    summary["failed"].append({
                        "account": account_label, "domain": full_domain,
                        "error_code": error_code, "message": err_msg
                    })
                    exit_code = 1

            time.sleep(0.5)

    print(f"\n{'='*60}")
    print("📊 执行摘要")
    print(f"{'='*60}")
    print(f"账号数:       {summary['total_accounts']}")
    print(f"域名总数:     {summary['total_domains']}")
    print(f"✅ 续期成功:   {len(summary['renewed'])}")
    print(f"⏳ 未达窗口:   {len(summary['not_yet_available'])}")
    print(f"⏭️  跳过:      {len(summary['skipped'])}")
    print(f"❌ 失败:       {len(summary['failed'])}")
    if summary["quota_warnings"]:
        print(f"⚠️  配额告警:   {len(summary['quota_warnings'])}")

    gh_output("renewed_count", str(len(summary["renewed"])))
    gh_output("failed_count", str(len(summary["failed"])))
    gh_output("summary_json", json.dumps(summary, ensure_ascii=False))

    if summary["failed"]:
        print(f"\n❌ 失败详情:")
        for item in summary["failed"]:
            domain = item.get("domain", "-")
            print(f"   - [{item.get('account', '?')}] {domain}: {item.get('message', item.get('reason', ''))}")

    sys.exit(exit_code)


if __name__ == "__main__":
    accounts_json = os.environ.get("ACCOUNTS", "[]")
    renew_days = int(os.environ.get("RENEW_DAYS_BEFORE", "30"))
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    if not accounts_json or accounts_json == "[]":
        print("❌ 环境变量 ACCOUNTS 未设置或为空")
        sys.exit(1)

    run_renewal(accounts_json, renew_days, dry_run)
