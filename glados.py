import os
import sys
import time

import requests


CHECKIN_URL = "https://glados.cloud/api/user/checkin"
STATUS_URL = "https://glados.cloud/api/user/status"
PUSHPLUS_URL = "https://www.pushplus.plus/send"
REQUEST_TIMEOUT = (10, 30)
MAX_ATTEMPTS = 3
RETRY_STATUS_CODES = {
    403,
    408,
    429,
    500,
    502,
    503,
    504,
    520,
    521,
    522,
    523,
    524,
}


class GladosApiError(RuntimeError):
    pass


def build_headers(cookie):
    return {
        "cookie": cookie,
        "referer": "https://glados.cloud/console/checkin",
        "origin": "https://glados.cloud",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/102.0.0.0 Safari/537.36"
        ),
        "content-type": "application/json;charset=UTF-8",
    }


def response_summary(response):
    content_type = response.headers.get("content-type", "unknown")
    preview = " ".join(response.text[:160].split())
    return (
        f"HTTP {response.status_code}, content-type={content_type}, "
        f"body={preview!r}"
    )


def request_json(session, method, url, cookie, payload=None):
    last_error = "unknown error"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = None
        try:
            response = session.request(
                method,
                url,
                headers=build_headers(cookie),
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("response JSON is not an object")
            return data
        except (requests.RequestException, ValueError) as error:
            if response is None:
                last_error = str(error)
                retryable = isinstance(
                    error, (requests.ConnectionError, requests.Timeout)
                )
            else:
                last_error = f"{error}; {response_summary(response)}"
                retryable = (
                    response.status_code in RETRY_STATUS_CODES
                    or response.status_code == 200
                )

            if attempt == MAX_ATTEMPTS or not retryable:
                break

            delay = 2 ** (attempt - 1)
            print(
                f"::warning::GLaDOS API 请求异常，第 {attempt}/{MAX_ATTEMPTS} "
                f"次失败，{delay} 秒后重试：{last_error}"
            )
            time.sleep(delay)

    raise GladosApiError(f"GLaDOS API 请求失败：{last_error}")


def checkin_account(session, cookie):
    checkin_data = request_json(
        session,
        "POST",
        CHECKIN_URL,
        cookie,
        payload={"token": "glados.cloud"},
    )
    status_data = request_json(session, "GET", STATUS_URL, cookie)

    try:
        account_data = status_data["data"]
        email = account_data["email"]
        left_days = str(account_data["leftDays"]).split(".")[0]
    except (KeyError, TypeError) as error:
        message = status_data.get("message", "状态响应缺少账户数据")
        raise GladosApiError(message) from error

    message = checkin_data.get("message")
    if not message:
        raise GladosApiError("签到响应缺少 message 字段")

    return email, message, left_days


def send_pushplus(token, title, content):
    try:
        response = requests.get(
            PUSHPLUS_URL,
            params={"token": token, "title": title, "content": content},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        print(f"::warning::PushPlus 推送失败：{error}")


def main():
    cookie_value = os.environ.get("GLADOS_COOKIE", "")
    cookies = [cookie.strip() for cookie in cookie_value.split("&") if cookie.strip()]
    pushplus_token = os.environ.get("PUSHPLUS_TOKEN", "").strip()

    if not cookies:
        print("::error::未获取到 GLADOS_COOKIE 变量")
        return 1

    successes = []
    failures = []

    with requests.Session() as session:
        for index, cookie in enumerate(cookies, start=1):
            try:
                email, message, left_days = checkin_account(session, cookie)
                result = f"{email}----结果--{message}----剩余({left_days})天"
                successes.append(result)
                print(result)
            except GladosApiError as error:
                result = f"账号 {index} 签到失败：{error}"
                failures.append(result)
                print(f"::error::{result}")

    if pushplus_token:
        title = "GLaDOS 签到异常" if failures else "GLaDOS 签到成功"
        send_pushplus(pushplus_token, title, "\n".join(successes + failures))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
