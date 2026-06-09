import os
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

# 脚本所在目录
script_dir = os.path.dirname(os.path.abspath(__file__))
NEW_M3U_PATH = os.path.join(script_dir, 'new.m3u')

# 延迟阈值（秒），超过此值的链接会被过滤
MAX_LATENCY_THRESHOLD = 10.0

# 读取前 N 字节判断 M3U8 格式（增大到 8KB，避免遗漏）
READ_BYTES = 8192

# 创建全局 Session，复用 TCP 连接，提高性能
session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Connection': 'keep-alive',
})
# 配置连接池大小
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
session.mount('http://', adapter)
session.mount('https://', adapter)


def parse_extvlcopt_headers(extvlcopt_line):
    """
    解析 EXTVLCOPT 行，提取 HTTP 请求头
    """
    headers = {}
    if not extvlcopt_line:
        return headers

    line_content = extvlcopt_line.replace('#EXTVLCOPT:', '').strip()
    options = line_content.split()

    for option in options:
        if '=' in option:
            key, value = option.split('=', 1)
            if key == 'http-user-agent':
                headers['User-Agent'] = value
            elif key in ('http-referrer', 'http-referer'):
                headers['Referer'] = value
            elif key == 'http-cookie':
                headers['Cookie'] = value
            elif key.startswith('http-'):
                header_key = key.replace('http-', '').replace('-', ' ').title().replace(' ', '-')
                headers[header_key] = value
    return headers


def test_link_optimized(url, extvlcopt_line=None):
    """
    优化版链接测试：仅一次 GET 请求，自动跟随重定向，读取前 N 字节判断是否为 M3U8
    返回 (是否有效, 最终URL, 响应时间秒数)
    """
    try:
        # 合并请求头
        custom_headers = parse_extvlcopt_headers(extvlcopt_line) if extvlcopt_line else {}
        req_headers = session.headers.copy()
        req_headers.update(custom_headers)

        start_time = time.time()
        resp = session.get(
            url,
            stream=True,
            timeout=10,
            allow_redirects=True,   # 自动跟随重定向
            headers=req_headers
        )
        elapsed = time.time() - start_time

        if resp.status_code != 200:
            resp.close()
            return False, resp.url, elapsed

        # 读取前 READ_BYTES 字节（只读一部分，不断开连接前关闭即可）
        content_chunk = b''
        for chunk in resp.iter_content(chunk_size=1024):
            content_chunk += chunk
            if len(content_chunk) >= READ_BYTES:
                break
        resp.close()   # 及时关闭，避免继续下载

        # 检查是否为 M3U8 播放列表
        try:
            text = content_chunk.decode('utf-8', errors='ignore')
            if '#EXTM3U' in text or '#EXTINF' in text:
                if elapsed > MAX_LATENCY_THRESHOLD:
                    return False, resp.url, elapsed
                return True, resp.url, elapsed
        except:
            pass
        return False, resp.url, elapsed

    except Exception as e:
        # 任何异常都视为无效
        return False, url, 0


def process_page(url):
    """
    处理单个 M3U 页面（不写磁盘，内存中处理）
    返回该页面中有效的链接列表，每个元素为 (extinf, extvlcopt, final_url, latency)
    """
    try:
        # 下载 M3U 文件
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        lines = resp.text.splitlines()
    except Exception as e:
        print(f"下载失败 {url}: {e}")
        return []

    links_to_test = []          # 待测试的链接列表
    current_group_title = ''    # 当前分组标题
    extinf = ''
    extvlcopt = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 处理 EXTINF 行
        if line.startswith("#EXTINF"):
            extinf = line
            # 提取 group-title
            match = re.search(r'group-title="([^"]*)"', line)
            if match:
                current_group_title = match.group(1)
            else:
                current_group_title = ''

            # 分组过滤（保持不变）：跳过 Hong Kong, Taiwan, China, Information
            if current_group_title in ['Hong Kong', 'Taiwan', 'China', 'Information']:
                print(f'已过滤分组: {current_group_title}')
                current_group_title = ''   # 标记跳过该分组下所有后续链接
            continue

        # 如果当前分组被跳过，则不处理任何链接
        if current_group_title == '':
            continue

        # 处理 EXTVLCOPT 行（可能包含自定义请求头）
        if line.startswith("#EXTVLCOPT:"):
            extvlcopt = line
            continue

        # 处理链接行
        if line.startswith(("http://", "https://", "rtmp://")):
            link = line
            # 域名黑名单过滤
            domain_match = re.search(r'://([^/]+)/', link)
            if domain_match:
                domain = domain_match.group(1)
                if domain in ["sc2022.stream-link.org", "39.134.24.162", "epg.pw"]:
                    print(f"域名黑名单，跳过: {link}")
                    extvlcopt = None
                    continue

            # 存入待测试列表
            links_to_test.append((extinf, extvlcopt, link))
            extvlcopt = None   # 重置，避免影响下一条链接

    if not links_to_test:
        print(f"{url} 没有需要测试的链接")
        return []

    print(f"{url} 共有 {len(links_to_test)} 个链接待测试，开始并发检测...")

    # 动态调整并发数（最大 10，最小 2，根据链接数量调整）
    max_workers = min(10, max(2, len(links_to_test) // 20 + 1))
    valid_results = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(test_link_optimized, link[-1], link[1]): link
            for link in links_to_test
        }
        for future in as_completed(future_to_item):
            extinf, extvlcopt, link = future_to_item[future]
            try:
                is_valid, final_url, latency = future.result()
                if is_valid:
                    valid_results.append((extinf, extvlcopt, final_url, latency))
                    print(f"✓ 有效: {link} ({latency:.2f}s)")
                else:
                    print(f"✗ 无效: {link}")
            except Exception as e:
                print(f"测试异常 {link}: {e}")

    print(f"{url} 处理完成，有效链接数: {len(valid_results)}")
    return valid_results


def main():
    # 要处理的 M3U 页面列表
    pages = [
        'https://iptv-org.github.io/iptv/index.country.m3u',
        'https://raw.githubusercontent.com/luongz/Japan-IPTV/main/jp.m3u',
        'https://raw.githubusercontent.com/akkradet/IPTV-THAI/refs/heads/master/FREETV.m3u',
    ]

    all_valid = []
    for page in pages:
        print(f"\n=== 处理页面: {page} ===")
        results = process_page(page)
        all_valid.extend(results)

    # 一次性写入最终文件
    print(f"\n总共获得 {len(all_valid)} 条有效链接，正在写入 {NEW_M3U_PATH} ...")
    with open(NEW_M3U_PATH, 'w', encoding='utf-8') as f:
        f.write("#EXTM3U\n")
        for extinf, extvlcopt, url, latency in all_valid:
            f.write(extinf + '\n')
            if extvlcopt:
                f.write(extvlcopt + '\n')
            f.write(url + '\n')
    print(f"完成！有效链接已保存至 {NEW_M3U_PATH}")


if __name__ == "__main__":
    main()
