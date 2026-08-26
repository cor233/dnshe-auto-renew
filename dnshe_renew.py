import os
import sys
import json
import time
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode

API_BASE = "https://api005.dnshe.com/index.php"
RATE_LIMIT_INTERVAL = 2.0


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
        self.last_request_time = 0

    def _throttle(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < RATE_LIMIT_INTERVAL:
            time.sleep(RATE_LIMIT_INTERVAL - elapsed)
        self.last_request_time = time.time()

    def request(self, endpoint, action, method="GET", data=None, params=None):
        url = f"{API_BASE}?m=domain_hub&endpoint={endpoint}&action={action}"
        if params:
            url += "&" + urlencode(params)
        while True:
            self._throttle()
            try:
                if method == "GET":
                    resp = requests.get(url, headers=self.headers, timeout=30)
                else:
                    resp = requests.post(url, headers=self.headers, json=data, timeout=30)
                resp.raise_for_status()
                result = resp.json()
            except requests.exceptions.RequestException as e:
                return {"success": False, "error_code": "network_error", "message": str(e)}

            if result.get("error_code") == "rate_limit_exceeded":
                reset_at_str = result.get("details", {}).get("reset_at")
                if reset_at_str:
                    try:
                        reset_at = datetime.strptime(reset_at_str, "%Y-%m-%d %H:%M:%S")
                        wait_sec = max(1, int((reset_at - datetime.now()).total_seconds()) + 1)
                        if wait_sec > 120:
                            return result
                        time.sleep(wait_sec)
                        continue
                    except ValueError:
                        pass
                time.sleep(5)
                continue
            return result

    def list_subdomains(self):
        return self.request("subdomains", "list", params={
            "per_page": 500,
            "sort_by": "expires_at",
            "sort_dir": "asc",
            "fields": "id,full_domain,status,expires_at,never_expires"
        })

    def renew_subdomain(self, subdomain_id):
        return self.request("subdomains", "renew", method="POST", data={
            "subdomain_id": subdomain_id
        })


def gh_output(name, value):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"{name}={value}\n")


def process_account(acc, idx, now):
    api_key = acc.get("key", "")
    api_secret = acc.get("secret", "")
    account_label = f"账号 #{idx}"

    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"📋 {account_label}")
    lines.append(f"{'='*60}")

    if not api_key or not api_secret:
        lines.append("⚠️  缺少 key 或 secret，跳过")
        return {
            "lines": lines,
            "renewed": [],
            "skipped": [],
            "failed": [{"account": account_label, "reason": "缺少认证信息"}],
            "not_yet_available": [],
            "manual_required": [],
            "insufficient_balance": [],
            "total_domains": 0,
            "exit_code": 1
        }

    client = DNSHEClient(api_key, api_secret)

    result = client.list_subdomains()
    if not result.get("success"):
        err_msg = result.get("message", result.get("error", "未知错误"))
        lines.append(f"❌ 获取子域名列表失败: {err_msg}")
        return {
            "lines": lines,
            "renewed": [],
            "skipped": [],
            "failed": [{"account": account_label, "reason": f"获取列表失败: {err_msg}"}],
            "not_yet_available": [],
            "manual_required": [],
            "insufficient_balance": [],
            "total_domains": 0,
            "exit_code": 1
        }

    all_subdomains = result.get("subdomains", [])

    if not all_subdomains:
        lines.append("ℹ️  该账号下没有子域名")
        return {
            "lines": lines,
            "renewed": [],
            "skipped": [],
            "failed": [],
            "not_yet_available": [],
            "manual_required": [],
            "insufficient_balance": [],
            "total_domains": 0,
            "exit_code": 0
        }

    lines.append(f"🔍 共发现 {len(all_subdomains)} 个子域名")

    renewed = []
    skipped = []
    failed = []
    not_yet_available = []
    manual_required = []
    insufficient_balance = []
    exit_code = 0

    for domain in all_subdomains:
        sid = domain.get("id")
        full_domain = domain.get("full_domain", "unknown")
        status = domain.get("status", "unknown")
        expires_at_str = domain.get("expires_at")
        never_expires = domain.get("never_expires", 0)

        prefix = f"  • {full_domain:<40}"

        if never_expires:
            lines.append(f"{prefix} ➜ 永久有效，跳过")
            skipped.append({"account": account_label, "domain": full_domain, "reason": "永久有效"})
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
        elif status == "active" and days_until_expire is not None and days_until_expire <= 180:
            should_renew = True
            reason = f"剩余{days_until_expire}天"

        if not should_renew:
            status_str = f"剩余{days_until_expire}天" if days_until_expire is not None else "无过期时间"
            lines.append(f"{prefix} ➜ 无需续期 ({status_str})")
            skipped.append({"account": account_label, "domain": full_domain, "reason": "无需续期"})
            continue

        lines.append(f"{prefix} ➜ {reason}，续期中... ")

        renew_result = client.renew_subdomain(sid)

        if renew_result.get("success"):
            prev_expires = renew_result.get("previous_expires_at", "unknown")
            new_expires = renew_result.get("new_expires_at", "unknown")
            remaining_days = renew_result.get("remaining_days", "unknown")
            charged = renew_result.get("charged_amount", 0)
            lines[-1] += f"✅ 成功! {prev_expires} → {new_expires}, 剩余: {remaining_days}天, 扣费: {charged}"
            renewed.append({
                "account": account_label,
                "domain": full_domain,
                "previous_expires_at": prev_expires,
                "new_expires_at": new_expires,
                "remaining_days": remaining_days,
                "charged": charged
            })
        else:
            error_code = renew_result.get("error_code", "")
            err_msg = renew_result.get("message", renew_result.get("error", "未知错误"))

            if error_code == "renewal_not_yet_available":
                remaining = renew_result.get("remaining_days") or renew_result.get("remaining_time") or "未知"
                lines[-1] += f"⏳ 未达续期窗口 (剩余 {remaining})"
                not_yet_available.append({
                    "account": account_label, "domain": full_domain, "message": err_msg, "remaining": remaining
                })
            elif error_code == "redemption_period_requires_administrator":
                lines[-1] += f"🔴 需人工处理: {err_msg}"
                manual_required.append({
                    "account": account_label, "domain": full_domain, "message": err_msg
                })
                exit_code = 1
            elif error_code == "insufficient_balance_for_redemption_renewal":
                lines[-1] += f"💰 余额不足: {err_msg}"
                insufficient_balance.append({
                    "account": account_label, "domain": full_domain, "message": err_msg
                })
                exit_code = 1
            else:
                lines[-1] += f"❌ 失败 [{error_code}]: {err_msg}"
                failed.append({
                    "account": account_label, "domain": full_domain,
                    "error_code": error_code, "message": err_msg
                })
                exit_code = 1

    return {
        "lines": lines,
        "renewed": renewed,
        "skipped": skipped,
        "failed": failed,
        "not_yet_available": not_yet_available,
        "manual_required": manual_required,
        "insufficient_balance": insufficient_balance,
        "total_domains": len(all_subdomains),
        "exit_code": exit_code
    }


def run_renewal(accounts_json):
    try:
        accounts = json.loads(accounts_json)
    except json.JSONDecodeError as e:
        print(f"❌ 账号配置 JSON 解析失败: {e}")
        sys.exit(1)

    if not isinstance(accounts, list):
        print("❌ 账号配置必须是 JSON 数组")
        sys.exit(1)

    now = datetime.now()
    summary = {
        "total_accounts": len(accounts),
        "total_domains": 0,
        "renewed": [],
        "skipped": [],
        "failed": [],
        "not_yet_available": [],
        "manual_required": [],
        "insufficient_balance": []
    }
    exit_code = 0

    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {
            executor.submit(process_account, acc, idx, now): idx
            for idx, acc in enumerate(accounts, 1)
        }
        for future in as_completed(futures):
            try:
                result = future.result(timeout=300)
            except Exception as e:
                idx = futures[future]
                result = {
                    "lines": [f"\n{'='*60}", f"📋 账号 #{idx}", f"{'='*60}", f"❌ 执行异常: {e}"],
                    "renewed": [],
                    "skipped": [],
                    "failed": [{"account": f"账号 #{idx}", "reason": str(e)}],
                    "not_yet_available": [],
                    "manual_required": [],
                    "insufficient_balance": [],
                    "total_domains": 0,
                    "exit_code": 1
                }
            for line in result["lines"]:
                print(line)
            summary["total_domains"] += result["total_domains"]
            summary["renewed"].extend(result["renewed"])
            summary["skipped"].extend(result["skipped"])
            summary["failed"].extend(result["failed"])
            summary["not_yet_available"].extend(result["not_yet_available"])
            summary["manual_required"].extend(result["manual_required"])
            summary["insufficient_balance"].extend(result["insufficient_balance"])
            if result["exit_code"] != 0:
                exit_code = 1

    print(f"\n{'='*60}")
    print("📊 执行摘要")
    print(f"{'='*60}")
    print(f"账号数:       {summary['total_accounts']}")
    print(f"域名总数:     {summary['total_domains']}")
    print(f"✅ 续期成功:   {len(summary['renewed'])}")
    print(f"⏳ 未达窗口:   {len(summary['not_yet_available'])}")
    print(f"⏭️  跳过:      {len(summary['skipped'])}")
    print(f"🔴 需人工处理: {len(summary['manual_required'])}")
    print(f"💰 余额不足:   {len(summary['insufficient_balance'])}")
    print(f"❌ 失败:       {len(summary['failed'])}")

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

    if not accounts_json or accounts_json == "[]":
        print("❌ 环境变量 ACCOUNTS 未设置或为空")
        sys.exit(1)

    run_renewal(accounts_json)
