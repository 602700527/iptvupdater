import os, shutil
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# 获取脚本所在的目录路径
script_dir = os.path.dirname(os.path.abspath(__file__))
new_file_path = os.path.join(script_dir, 'new.m3u')

# 延迟阈值（秒），超过这个值的链接会被过滤
MAX_LATENCY_THRESHOLD = 5.0

# 清空旧数据
with open(new_file_path, 'w') as file:
    pass  # 清空旧数据

# 解析EXTVLCOPT行获取请求头
def parse_extvlcopt_headers(extvlcopt_line):
    """
    解析EXTVLCOPT行，提取HTTP请求头

    Args:
    extvlcopt_line: EXTVLCOPT行，如 '#EXTVLCOPT:http-user-agent=Mozilla/5.0'

    Returns:
    dict: 包含请求头的字典
    """
    headers = {}

    if not extvlcopt_line:
        return headers

    # 移除#EXTVLCOPT:前缀
    line_content = extvlcopt_line.replace('#EXTVLCOPT:', '').strip()

    # 按空格分割多个选项
    options = line_content.split()

    for option in options:
        # 分割键值对
        if '=' in option:
            key, value = option.split('=', 1)

            # 将EXTVLCOPT的选项键转换为标准HTTP请求头键
            if key == 'http-user-agent':
                headers['User-Agent'] = value
            elif key == 'http-referrer' or key == 'http-referer':
                headers['Referer'] = value
            elif key == 'http-cookie':
                headers['Cookie'] = value
            elif key.startswith('http-'):
                # 处理其他http-开头的头部
                header_key = key.replace('http-', '').replace('-', ' ').title().replace(' ', '-')
                headers[header_key] = value

    return headers

# 追踪重定向并测试链接的函数
def test_link_with_redirect(url, extvlcopt_line=None, max_redirects=10):
    """
    追踪播放链接到最后一个重定向，并测试是否取得M3U8播放列表

    Args:
    url: 原始URL
    extvlcopt_line: EXTVLCOPT行，可能包含请求头信息
    max_redirects: 最大重定向次数

    Returns:
    tuple: (是否有效, 最终URL, 响应时间秒数)
    """
    try:
        # 解析EXTVLCOPT行获取请求头
        custom_headers = {}
        if extvlcopt_line:
            custom_headers = parse_extvlcopt_headers(extvlcopt_line)

        # 基础请求头
        base_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }

        # 合并请求头，自定义请求头优先
        headers = {**base_headers, **custom_headers}

        current_url = url
        redirect_count = 0

        # 追踪重定向
        while redirect_count < max_redirects:
            try:
                # 发送HEAD请求追踪重定向
                response = requests.head(
                    current_url,
                    allow_redirects=False,
                    timeout=10,
                    headers=headers
                )

                # 检查是否是重定向
                if response.status_code in [301, 302, 303, 307, 308]:
                    if 'Location' in response.headers:
                        redirect_count += 1
                        current_url = response.headers['Location']
                        print(f"重定向 {redirect_count}: {current_url}")

                        # 如果是相对路径，转换为绝对路径
                        if current_url.startswith('/'):
                            from urllib.parse import urljoin
                            current_url = urljoin(url, current_url)

                        continue
                    else:
                        print(f"重定向响应但没有Location头: {response.status_code}")
                        break
                else:
                    # 不是重定向，跳出循环
                    break

            except requests.RequestException as e:
                print(f"追踪重定向时出错: {e}")
                return False, current_url, 0

        final_url = current_url

        # 尝试获取内容
        try:
            response = requests.get(
                final_url,
                timeout=10,
                headers=headers,
                stream=True  # 流模式，只读取部分内容
            )

            if response.status_code == 200:
                # 获取响应时间
                response_time = response.elapsed.total_seconds()

                # 读取前1024字节检查是否是M3U8格式
                content = response.raw.read(1024).decode('utf-8', errors='ignore')

                # 检查是否是M3U8播放列表
                if '#EXTM3U' in content or '#EXTINF' in content:
                    print(f"成功获取M3U8播放列表: {final_url} (响应时间: {response_time:.2f}秒)")

                    # 检查响应时间是否超过阈值
                    if response_time > MAX_LATENCY_THRESHOLD:
                        print(f"延迟过高，已过滤: {response_time:.2f}秒 > {MAX_LATENCY_THRESHOLD}秒")
                        return False, final_url, response_time

                    return True, final_url, response_time
                else:
                    print(f"内容不是有效的M3U8格式: {final_url}")
                    return False, final_url, response_time
            else:
                print(f"HTTP错误 {response.status_code}: {final_url}")
                return False, final_url, 0

        except requests.RequestException as e:
            print(f"获取M3U8内容时出错: {e}")
            return False, final_url, 0

    except Exception as e:
        print(f"测试链接时发生未知错误: {e}")
        return False, url, 0

# 将有效链接写入文件
def write_valid_link(new_m3u_path, extinf, extvlcopt, link, is_valid, response_time=0):
    if is_valid:
        with open(new_m3u_path, 'a') as new_m3u:
            if extvlcopt:
                new_m3u.write(extinf + '\n')
                new_m3u.write(extvlcopt + '\n')
                new_m3u.write(link + '\n')
                print(f"有效链接已写入：{link} (响应时间: {response_time:.2f}秒)")
            else:
                new_m3u.write(extinf + '\n')
                new_m3u.write(link + '\n')
                print(f"有效链接已写入：{link} (响应时间: {response_time:.2f}秒)")

#提取分组名
def extract_group_title_and_name(extinf_line):
    # 正则表达式匹配group-title和逗号后面的名称
    match = re.search(r'group-title="([^"]+)".*?,([^,]+)', extinf_line)
    if match:
        group_title = match.group(1)
        name = match.group(2).strip()
        return group_title, name
    else:
        return None, None

# 处理页面并下载M3U文件
def process_page(url):
    file_name = os.path.basename(url)
    m3u_file_path = os.path.join(script_dir, file_name)
    new_m3u_path = os.path.join(script_dir, 'new.m3u')
    extinf = ""
    extvlcopt = None
    current_group_title = ''
    
    try:
        response = requests.get(url)
        with open(m3u_file_path, 'w') as m3u_file:
            m3u_file.write(response.text)
            print(f"M3U文件 {url} 已下载并保存为 {m3u_file_path}")

        links_to_test = []  # 存储待测试的链接和相关信息

        with open(m3u_file_path, 'r') as m3u_file:
            for line in m3u_file:
                line = line.strip()
                if line.startswith("#EXTINF"):
                    extinf = line
                    # 尝试匹配分组标题
                    group_title_match = re.search(r'group-title="([^"]*)"', line)
                    if group_title_match:
                        current_group_title = group_title_match.group(1)
                    
                    # 检查当前分组标题是否应该被过滤
                    if current_group_title in ['Hong Kong', 'Taiwan', 'China', 'Information']:
                        print('+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-已过滤该分组：', current_group_title, '+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+')
                        current_group_title = ''  # 重置以跳过该分组下的所有链接
                        continue  # 跳过当前的EXTINF行
                
                # 只有在当前分组没有被过滤时，才处理EXTVLCOPT和链接
                if current_group_title:
                    if line.startswith("#EXTVLCOPT:"):
                        extvlcopt = line
                    elif line.startswith(("http://", "https://", "rtmp://")):
                        link = line
                        domain = re.search(r'://([^/]+)/', line)
                        if domain and domain.group(1) not in ["sc2022.stream-link.org", "39.134.24.162", "epg.pw"]:
                            links_to_test.append((extinf, extvlcopt, link))
                            # 重置extvlcopt，避免影响下一个链接
                            extvlcopt = None

        # 使用线程池测试链接
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(test_link_with_redirect, link[-1], link[1]): link for link in links_to_test}
            
            for future in as_completed(futures):
                link_data = futures[future]
                result = future.result()
                if result[0]:  # 如果链接有效
                    write_valid_link(new_m3u_path, link_data[0], link_data[1], link_data[-1], True, result[2])
                else:
                    print(f"无效链接：{link_data[-1]}")

    except requests.RequestException as e:
        print(f"无法下载M3U文件 {url}: {e}")

# 定义要处理的页面数组
pages = [
    'https://iptv-org.github.io/iptv/index.country.m3u',
    'https://raw.githubusercontent.com/luongz/Japan-IPTV/main/jp.m3u',  # 日本源
    'https://raw.githubusercontent.com/akkradet/IPTV-THAI/refs/heads/master/FREETV.m3u',  # 泰国源
]

# 处理每个页面
for page in pages:
    # 处理页面
    process_page(page)



