import sys, os, re

# 打包后静默运行，不弹命令行窗口
if getattr(sys, 'frozen', False):
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import requests
import base64
from datetime import datetime, timedelta, timezone
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import threading
import time
import os
import sys
import json
import webbrowser
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# 判断是否为打包后的程序
if getattr(sys, 'frozen', False):
    # 打包后的程序
    BASE_DIR = sys._MEIPASS
    SAVE_DIR = os.path.dirname(sys.executable)  # exe所在目录，用于写文件
else:
    # 开发环境
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    SAVE_DIR = BASE_DIR

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'))
CORS(app)

# ====================== 【核心配置】 ======================
MY_ACCOUNT = "V0013992"
MY_PASSWORD = "Xs123456"
PUBLIC_KEY = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCctQTweXAiaQ3ct5bhj6nyisOQiGmgC/hUdK+QO9I9DudcQSUMxIXvMtpiogB9RWkAUC4b86x7SiGD6aCp7PbTspd5fLf8F6LUIj/BtmktQq7JNsShjAWBxCkE49HIIvPvl9rt8lO7MkgS2vUT04tEYeu/62ltOc3BljJXoPC4pQIDAQAB"

AUTO_REGIONS = ["湖南", "湖北", "新疆", "河北", "安徽"]

# 可选省份列表
ALL_PROVINCES = ["河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北",
                 "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏",
                 "新疆", "北京", "上海", "天津", "重庆"]

# 网点配置 - 请根据实际网点名称和ID修改
ALL_NETWORKS = {
    "江南": "713226235836239872",
    "非凡": "823427370722664448",
    "讯服": "823427183694450688",
    "江北": "713226114964791296",
    "零担": "740441957821714432",
    "兴兴": "823426759423827968",
    "广宏": "823426927623806976"
}
# 默认选中的网点
SELECTED_NETWORKS = ["零担"]

BASE_URL = "https://sdm.etransfar.com/jbl/api"
LOGIN_URL = "https://sdm.etransfar.com/jbl/api/login/?_allow_anonymous=true"
QUERY_URL = "https://sdm.etransfar.com/jbl/api/module-data/purchase_order/page"
RECEIPT_QUERY_URL = "https://sdm.etransfar.com/jbl/api/module-data/receive_management/page"
KPI_QUERY_URL = "https://sdm.etransfar.com/jbl/api/module-data/supplier_abnormal_tabul/page"
KPI_DETAIL_URL = "https://sdm.etransfar.com/jbl/api/module-data/supplier_abnormal/supplier_abnormal/375549423855472640"

# 全局缓存数据
cache_data = {
    "manual_query": {},
    "auto_monitor": {},
    "region_stats": {},
    "today_unsigned": {},
    "tomorrow_unsigned": {},
    "yesterday_unsigned": {},
    "sender_region_orders": {},
    "today_orders": {},
    "kpi_penalty": {},
    "intransit_today_count": 0,
    "last_update": None
}
cache_lock = threading.Lock()


# ====================== HTTP连接池（线程本地复用，避免重复TLS握手） ======================
_session_local = threading.local()

def _sess():
    """获取线程本地的 requests Session，自动复用TCP/TLS连接"""
    if not hasattr(_session_local, 's'):
        s = requests.Session()
        # 增加连接池：每个host保持5个连接，最多10个
        from requests.adapters import HTTPAdapter
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10)
        s.mount('https://', adapter)
        s.mount('http://', adapter)
        _session_local.s = s
    return _session_local.s


# ====================== Token缓存 ======================
_token_cache = {"token": None, "expire_time": 0}
_token_lock = threading.Lock()

def get_token():
    """获取缓存的token，未过期直接复用"""
    with _token_lock:
        now = time.time()
        if _token_cache["token"] and now < _token_cache["expire_time"]:
            return _token_cache["token"]
        token = login()
        if token:
            _token_cache["token"] = token
            _token_cache["expire_time"] = now + 1800
        return token


# ====================== RSA公钥预计算 ======================
_RSA_KEY = RSA.importKey(f"-----BEGIN PUBLIC KEY-----\n{PUBLIC_KEY}\n-----END PUBLIC KEY-----")
_RSA_CIPHER = PKCS1_v1_5.new(_RSA_KEY)


# ====================== 工具函数 ======================
def rsa_encrypt(password):
    encrypted = _RSA_CIPHER.encrypt(password.encode())
    return base64.b64encode(encrypted).decode()


def login():
    try:
        payload = {
            "name": MY_ACCOUNT,
            "password": rsa_encrypt(MY_PASSWORD),
            "rememberMe": True,
            "imageCode": None,
            "loginBindingParameters": {}
        }
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://sdm.etransfar.com/jbl/"
        }
        resp = _sess().post(LOGIN_URL, json=payload, headers=headers, timeout=15)
        data = resp.json()
        if data.get("status") == "login":
            return data["token"]
        return None
    except:
        return None


def _parse_cargo_details(order):
    """解析订单中的货物详情，优先使用 packaging_data_json，回退到顶层字段"""
    cargo_list = []
    packaging_json = order.get("packaging_data_json")
    if packaging_json:
        try:
            packages = json.loads(packaging_json) if isinstance(packaging_json, str) else packaging_json
            for pkg in packages:
                cargo_list.append({
                    "type": pkg.get("package_property_desc", "未知"),
                    "count": float(pkg.get("packaging_num", 0))
                })
        except Exception:
            pass

    # 回退：如果 packaging_data_json 解析为空，使用顶层字段
    if not cargo_list:
        prop_desc = order.get("package_property_desc", "")
        pkg_num = order.get("packaging_num")
        if prop_desc and pkg_num is not None:
            try:
                pkg_num = float(pkg_num)
            except (ValueError, TypeError):
                pkg_num = 0
            # package_property_desc 可能是逗号分隔的多个类型，取第一个作为默认
            types = [t.strip() for t in prop_desc.split(",") if t.strip()]
            if types:
                cargo_list.append({"type": types[0], "count": pkg_num})

    return cargo_list


def format_time(time_str):
    if not time_str:
        return None
    try:
        dt_utc = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        dt_bj = dt_utc.astimezone(timezone(timedelta(hours=8)))
        return dt_bj.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return time_str


_PROVINCE_LIST = ["河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北",
                  "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏",
                  "新疆", "北京", "上海", "天津", "重庆"]
_PROVINCE_SET = frozenset(_PROVINCE_LIST)
_PROVINCE_RE = re.compile('(' + '|'.join(sorted(_PROVINCE_LIST, key=len, reverse=True)) + ')')


def extract_province(city_str):
    if not city_str:
        return "未知"
    if "省" in city_str:
        return city_str.split("省")[0] + "省"
    m = _PROVINCE_RE.search(city_str)
    return m.group(1) if m else (city_str[:6] if len(city_str) > 6 else city_str)


# ====================== 数据获取函数 ======================
def query_orders(token, region):
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8"
        }
        payload = {
            "direction": "DESC",
            "property": "id",
            "fromClientType": "pc",
            "ignoreField": False,
            "number": 0,
            "dynamicFormCode": "purchase_order",
            "rules": [
                {"field": "receive_region_code_show", "option": "LIKE_ANYWHERE", "values": [region]},
                {"field": "status_dk_show", "option": "EQ", "values": ["待配载"]}
            ],
            "size": 5000,
            "sorts": [
                {"property": "required_arrival_date", "direction": "ASC"},
                {"property": "receive_region_code", "direction": "ASC"}
            ],
            "specialConditions": []
        }
        resp = _sess().post(QUERY_URL, json=payload, headers=headers, timeout=20)
        return resp.json().get("content", [])
    except:
        return []


def get_manual_query_data(token):
    all_orders = []
    for region in AUTO_REGIONS:
        orders = query_orders(token, region)
        for o in orders:
            all_orders.append({
                "order_no": o.get("source_order_no", "无"),
                "region": o.get("receive_region_code_show", "无"),
                "customer": o.get("customer", "无"),
                "weight": float(o.get("total_weight", 0)),
                "remark": o.get("remark") or "",
                "warehouse": o.get("all_send_storage_code_show", "无"),
                "order_date": format_time(o.get("order_date")),
                "urgent_flag_custom": o.get("urgent_flag_custom", "无"),
                "the_way_flag_custom": o.get("the_way_flag_custom", "无"),
                "cargo_details": _parse_cargo_details(o)
            })
    total_weight = sum(o["weight"] for o in all_orders)
    return {
        "orders": all_orders,
        "total_count": len(all_orders),
        "total_weight": round(total_weight, 2)
    }


def get_auto_monitor_data(token):
    region_stats = []
    for region in AUTO_REGIONS:
        orders = query_orders(token, region)
        total_weight = sum(float(o.get("total_weight", 0)) for o in orders)
        region_stats.append({
            "region": region,
            "count": len(orders),
            "weight": round(total_weight, 2)
        })
    total_count = sum(r["count"] for r in region_stats)
    total_weight = sum(r["weight"] for r in region_stats)
    return {
        "regions": region_stats,
        "total_count": total_count,
        "total_weight": round(total_weight, 2)
    }


def get_region_stats_data(token):
    regions = AUTO_REGIONS.copy()  # 使用配置的省份
    all_orders = []
    region_details = []
    for region in regions:
        orders = query_orders(token, region)
        total_weight = sum(float(o.get("total_weight", 0)) for o in orders)
        region_details.append({
            "region": region,
            "count": len(orders),
            "weight": round(total_weight, 2)
        })
        for o in orders:
            all_orders.append({
                "order_no": o.get("source_order_no", "无"),
                "region": o.get("receive_region_code_show", "无"),
                "customer": o.get("customer", "无"),
                "weight": float(o.get("total_weight", 0)),
                "remark": o.get("remark") or "",
                "warehouse": o.get("all_send_storage_code_show", "无"),
                "order_date": format_time(o.get("order_date")),
                "urgent_flag_custom": o.get("urgent_flag_custom", "无"),
                "the_way_flag_custom": o.get("the_way_flag_custom", "无"),
                "cargo_details": _parse_cargo_details(o)
            })
    total_count = sum(r["count"] for r in region_details)
    total_weight = sum(r["weight"] for r in region_details)
    return {
        "region_details": region_details,
        "orders": all_orders,
        "total_count": total_count,
        "total_weight": round(total_weight, 2)
    }


def _fetch_region_orders(token, region):
    """并行获取单个省份的待配载订单（线程安全）"""
    orders = query_orders(token, region)
    total_weight = sum(float(o.get("total_weight", 0)) for o in orders)
    parsed_orders = [{
        "order_no": o.get("source_order_no", "无"),
        "region": o.get("receive_region_code_show", "无"),
        "customer": o.get("customer", "无"),
        "weight": float(o.get("total_weight", 0)),
        "remark": o.get("remark") or "",
        "warehouse": o.get("all_send_storage_code_show", "无"),
        "order_date": format_time(o.get("order_date")),
        "urgent_flag_custom": o.get("urgent_flag_custom", "无"),
        "the_way_flag_custom": o.get("the_way_flag_custom", "无"),
        "cargo_details": _parse_cargo_details(o)
    } for o in orders]
    return {
        "region": region,
        "count": len(orders),
        "weight": round(total_weight, 2),
        "orders": parsed_orders
    }


def get_pending_orders_unified(token):
    """统一获取待配载数据：合并manual_query + auto_monitor + region_stats，每个省份只查一次API"""
    with ThreadPoolExecutor(max_workers=min(len(AUTO_REGIONS), 3)) as executor:
        futures = {executor.submit(_fetch_region_orders, token, r): r for r in AUTO_REGIONS}
        region_results = {}
        for future in as_completed(futures):
            region = futures[future]
            try:
                region_results[region] = future.result()
            except Exception as e:
                print(f"查询省份 {region} 失败: {e}")
                region_results[region] = {"region": region, "count": 0, "weight": 0, "orders": []}

    # 按 AUTO_REGIONS 顺序组装结果
    region_stats = [region_results[r] for r in AUTO_REGIONS]
    all_orders = []
    region_details = []
    for r in region_stats:
        region_details.append({"region": r["region"], "count": r["count"], "weight": r["weight"]})
        all_orders.extend(r["orders"])

    total_count = sum(r["count"] for r in region_stats)
    total_weight = sum(r["weight"] for r in region_stats)

    manual_query = {"orders": all_orders, "total_count": total_count, "total_weight": round(total_weight, 2)}
    auto_monitor = {"regions": [{"region": r["region"], "count": r["count"], "weight": r["weight"]} for r in region_stats],
                    "total_count": total_count, "total_weight": round(total_weight, 2)}
    region_stats_data = {"region_details": region_details, "orders": all_orders, "total_count": total_count, "total_weight": round(total_weight, 2)}

    return manual_query, auto_monitor, region_stats_data


def get_today_utc_range():
    """获取今日北京时间范围的UTC时间"""
    bj_tz = timezone(timedelta(hours=8))
    today_bj = datetime.now(bj_tz).date()
    # 北京时间当天0点 = UTC前一天16:00
    today_start = datetime(today_bj.year, today_bj.month, today_bj.day, 0, 0, 0) - timedelta(hours=8)
    # 北京时间当天23:59:59 = UTC当天15:59:59
    today_end = datetime(today_bj.year, today_bj.month, today_bj.day, 23, 59, 59) - timedelta(hours=8)
    return [
        today_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        today_end.strftime("%Y-%m-%dT%H:%M:%S.999Z")
    ]


def get_unsigned_orders_data(token, day_offset=0):
    """获取未签收订单数据
    day_offset: 0=今天, 1=明天, -1=昨天
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    # 使用北京时间计算日期
    bj_tz = timezone(timedelta(hours=8))
    now_bj = datetime.now(bj_tz)
    target_date = (now_bj + timedelta(days=day_offset)).date()
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0) - timedelta(hours=8)
    end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59) - timedelta(hours=8)

    # 获取网点ID列表
    network_ids = [ALL_NETWORKS[n] for n in SELECTED_NETWORKS if n in ALL_NETWORKS]

    payload = {
        "debugFlag": False,
        "developmentSystemId": None,
        "direction": "DESC",
        "dynamicFormCode": "stowage_sign_receipt",
        "fromClientType": "pc",
        "number": 0,
        "property": "id",
        "rules": [
            {"field": "delivery_date", "option": "BTS", "values": [
                start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                end.strftime("%Y-%m-%dT%H:%M:%S.999Z")
            ]},
            {"field": "k_contract_line_a.network", "option": "IN", "values": network_ids}
        ],
        "size": 100,
        "sorts": [{"property": "receive_time", "direction": "DESC"}],
        "specialConditions": []
    }

    try:
        resp = _sess().post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
        orders = resp.json().get("content", [])
    except:
        orders = []

    unsigned_orders = []
    all_provinces = set()
    for item in orders:
        status = item.get("status_dk_show", "")
        if status != "已签收" and status != "已回单确认":
            # 获取完整地址信息（省市区的所有字段）
            province = item.get("province_id_show", "")  # 省
            city = item.get("city_id_show", "")  # 市
            district = item.get("district_id_show", "")  # 区/县
            street = item.get("street_id_show", "")  # 街道

            # 完整地址
            full_address = item.get("receive_address", "")

            # 提取省份（如果province字段为空，则从city中提取）
            if not province and city:
                province = extract_province(city)

            if province:
                all_provinces.add(province)

            unsigned_orders.append({
                "order_no": item.get("source_order_no", "无"),
                "receive_name": item.get("receive_name", ""),  # 收货方名称
                "receiver_phone": item.get("receiver_phone", ""),  # 收货方电话
                "province": province,  # 省
                "city": city,  # 市
                "district": district,  # 区/县
                "street": street,  # 街道
                "detailed_address": item.get("detailed_address", full_address),  # 详细地址
                "address": city + (district if district else ""),  # 显示用地址
                "status": status,
                "receive_time": format_time(item.get("receive_time")),
                "delivery_date": format_time(item.get("delivery_date")),  # 需求到货时间
                "signed_weight": float(item.get("stowage_all_weight", 0) or 0)  # 总重量
            })

    return {
        "total_orders": len(orders),
        "unsigned_count": len(unsigned_orders),
        "unsigned_orders": unsigned_orders,
        "provinces": sorted(list(all_provinces))
    }


def get_today_unsigned_data(token):
    return get_unsigned_orders_data(token, day_offset=0)


def get_tomorrow_unsigned_data(token):
    return get_unsigned_orders_data(token, day_offset=1)


def get_yesterday_unsigned_data(token):
    return get_unsigned_orders_data(token, day_offset=-1)


def query_orders_by_sender_region(token, region):
    """按发货地址省份查询待配载订单"""
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8"
        }
        payload = {
            "direction": "DESC",
            "property": "id",
            "fromClientType": "pc",
            "ignoreField": False,
            "number": 0,
            "dynamicFormCode": "purchase_order",
            "rules": [
                {"field": "send_region_code_show", "option": "LIKE_ANYWHERE", "values": [region]},
                {"field": "status_dk_show", "option": "EQ", "values": ["待配载"]}
            ],
            "size": 5000,
            "sorts": [
                {"property": "order_date", "direction": "DESC"}
            ],
            "specialConditions": []
        }
        resp = _sess().post(QUERY_URL, json=payload, headers=headers, timeout=20)
        return resp.json().get("content", [])
    except:
        return []


def _fetch_sender_region_orders(token, region):
    """并行获取单个发货省份的待配载订单（线程安全）"""
    orders = query_orders_by_sender_region(token, region)
    total_weight = sum(float(o.get("total_weight", 0)) for o in orders)
    parsed_orders = [{
        "order_no": o.get("source_order_no", "无"),
        "sender_region": o.get("send_region_code_show", "无"),
        "receive_region": o.get("receive_region_code_show", "无"),
        "customer": o.get("customer", "无"),
        "weight": float(o.get("total_weight", 0)),
        "total_qty": float(o.get("total_qty", 0)),
        "stowage_weight": float(o.get("stowage_all_weight", 0) or 0),
        "warehouse": o.get("all_send_storage_code_show", "无"),
        "create_time": format_time(o.get("order_date")),
        "on_the_way": o.get("the_way_flag_custom", "无"),
        "cargo_details": _parse_cargo_details(o)
    } for o in orders]
    return {"region": region, "count": len(orders), "weight": round(total_weight, 2), "orders": parsed_orders}


def get_sender_region_orders_data(token):
    """获取发货地址为配置省份的待配载订单（并行查询）"""
    regions = AUTO_REGIONS.copy()
    with ThreadPoolExecutor(max_workers=min(len(regions), 3)) as executor:
        futures = {executor.submit(_fetch_sender_region_orders, token, r): r for r in regions}
        region_results = {}
        for future in as_completed(futures):
            region = futures[future]
            try:
                region_results[region] = future.result()
            except Exception as e:
                print(f"查询发货省份 {region} 失败: {e}")
                region_results[region] = {"region": region, "count": 0, "weight": 0, "orders": []}

    region_details = []
    all_orders = []
    for r in regions:
        result = region_results[r]
        region_details.append({"region": result["region"], "count": result["count"], "weight": result["weight"]})
        all_orders.extend(result["orders"])

    total_count = sum(rd["count"] for rd in region_details)
    total_weight = sum(rd["weight"] for rd in region_details)

    return {
        "region_details": region_details,
        "orders": all_orders,
        "total_count": total_count,
        "total_weight": round(total_weight, 2)
    }


def get_today_orders_data(token):
    """获取今日配载订单统计（配载时间为今天的订单）"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    bj_tz = timezone(timedelta(hours=8))
    now_bj = datetime.now(bj_tz)
    today_bj = now_bj.date()
    day_start_utc = datetime(today_bj.year, today_bj.month, today_bj.day, 0, 0, 0) - timedelta(hours=8)
    day_end_utc = datetime(today_bj.year, today_bj.month, today_bj.day, 23, 59, 59) - timedelta(hours=8)

    network_ids = [ALL_NETWORKS[n] for n in SELECTED_NETWORKS if n in ALL_NETWORKS]

    payload = {
        "debugFlag": False,
        "developmentSystemId": None,
        "direction": "DESC",
        "dynamicFormCode": "stowage_sign_receipt",
        "fromClientType": "pc",
        "number": 0,
        "property": "id",
        "rules": [
            {"field": "exe_pur_order_b.order_date", "option": "BTS", "values": [
                day_start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                day_end_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            ]},
            {"field": "k_contract_line_a.network", "option": "IN", "values": network_ids}
        ],
        "size": 999,
        "sorts": [{"property": "order_date", "direction": "DESC"}],
        "specialConditions": []
    }

    try:
        resp = _sess().post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
        orders = resp.json().get("content", [])
    except:
        orders = []

    total_weight = 0
    order_list = []
    region_map = {}

    for o in orders:
        w = float(o.get("stowage_all_weight", 0) or 0)
        total_weight += w
        province = o.get("province_id_show", "未知") or "未知"

        region_map.setdefault(province, {"count": 0, "weight": 0})
        region_map[province]["count"] += 1
        region_map[province]["weight"] += w

        order_list.append({
            "order_no": o.get("source_order_no", "无"),
            "receive_name": o.get("receive_name", ""),
            "province": province,
            "weight": w,
            "create_time": format_time(o.get("exe_pur_order_b", {}).get("order_date", ""))
        })

    region_details = [{"region": r, "count": v["count"], "weight": round(v["weight"], 2)} for r, v in region_map.items()]

    return {
        "total_count": len(orders),
        "total_weight": round(total_weight, 2),
        "region_details": region_details,
        "detail_orders": order_list
    }


def get_intransit_today_count(token):
    """获取在途订单数量（配载单状态=已配载/已发车确认，后台缓存用）"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    network_ids = [ALL_NETWORKS[n] for n in SELECTED_NETWORKS if n in ALL_NETWORKS]
    if not network_ids:
        return 0

    payload = {
        "debugFlag": False,
        "developmentSystemId": None,
        "dynamicFormCode": "stowage_sign_receipt",
        "fromClientType": "pc",
        "number": 0,
        "property": "id",
        "rules": [
            {"field": "status_dk", "option": "IN", "values": ["WAITDELIVER", "DEPARTRUECONFIR"]},
            {"field": "k_contract_line_a.network", "option": "IN", "values": network_ids}
        ],
        "size": 9999,
        "sorts": [],
        "specialConditions": []
    }
    try:
        resp = _sess().post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
        if resp.status_code != 200:
            print(f"查询在途订单数量失败: HTTP {resp.status_code}")
            return 0
        orders = resp.json().get("content", [])
        return len(orders)
    except Exception as e:
        print(f"查询在途订单数量失败: {e}")
        return 0


def get_all_intransit_orders(token, network_names):
    """获取所有在途订单详情（弹窗用，按需查询），状态=已配载/已发车确认，按要求到货时间升序"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    network_ids = [ALL_NETWORKS[n] for n in network_names if n in ALL_NETWORKS]
    if not network_ids:
        return {"orders": [], "total_count": 0, "statuses": []}

    payload = {
        "debugFlag": False,
        "developmentSystemId": None,
        "dynamicFormCode": "stowage_sign_receipt",
        "fromClientType": "pc",
        "number": 0,
        "property": "id",
        "rules": [
            {"field": "status_dk", "option": "IN", "values": ["WAITDELIVER", "DEPARTRUECONFIR"]},
            {"field": "k_contract_line_a.network", "option": "IN", "values": network_ids}
        ],
        "size": 9999,
        "sorts": [{"property": "delivery_date", "direction": "ASC"}],
        "specialConditions": []
    }
    try:
        resp = _sess().post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"查询所有在途订单失败: HTTP {resp.status_code}")
            return {"orders": [], "total_count": 0, "statuses": []}
        orders = resp.json().get("content", [])
    except Exception as e:
        print(f"查询所有在途订单失败: {e}")
        return {"orders": [], "total_count": 0, "statuses": []}

    parsed_orders = []
    all_statuses = set()
    for item in orders:
        status = item.get("status_dk_show", "") or item.get("status_dk", "")
        all_statuses.add(status)

        province = item.get("province_id_show", "") or ""
        city = item.get("city_id_show", "") or ""
        district = item.get("district_id_show", "") or ""
        detailed_addr = item.get("detailed_address", "") or item.get("receive_address", "") or ""
        full_address = f"{province}{city}{district} {detailed_addr}".strip()

        parsed_orders.append({
            "order_no": item.get("source_order_no", ""),
            "driver_name": item.get("carrier_name", ""),
            "customer_name": (item.get("exe_pur_order_b") or {}).get("customer", ""),
            "salesman": item.get("salesman_name", ""),
            "status": status,
            "created_date": format_time(item.get("created_date")),
            "receive_address": full_address,
            "receiver": item.get("receiver_name", "") or item.get("receiver", ""),
            "delivery_date": format_time(item.get("delivery_date")),
            "weight": float(item.get("stowage_all_weight", 0) or 0),
            "province": province,
        })

    return {
        "orders": parsed_orders,
        "total_count": len(parsed_orders),
        "statuses": sorted(list(all_statuses))
    }


def extract_penalty_score(description):
    """从KPI处罚描述中提取扣分数"""
    if not description:
        return 0
    total = 0
    # 按优先级匹配，避免重复匹配
    # 优先匹配 "扣绩效考核X分" 或 "扣绩效考核X分/单"
    m = re.search(r'扣绩效考核(\d+\.?\d*)分', description)
    if m:
        return float(m.group(1))
    # 匹配 "考核记X分" 或 "考核X分"
    m = re.search(r'考核[记扣]?(\d+\.?\d*)分', description)
    if m:
        return float(m.group(1))
    # 匹配 "扣X分"
    m = re.search(r'扣(\d+\.?\d*)分', description)
    if m:
        return float(m.group(1))
    return 0


def extract_date_from_exception_no(exception_no):
    """从异常编号中提取日期，如 AB202605270044 -> 2026-05-27"""
    if not exception_no:
        return None
    m = re.match(r'AB(\d{4})(\d{2})(\d{2})', exception_no)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def get_kpi_cycle_periods():
    """获取两个KPI考核周期（当月23号到次月22号）"""
    bj_tz = timezone(timedelta(hours=8))
    now_bj = datetime.now(bj_tz)

    # 当前周期：如果今天 >= 23号，当前周期从本月23号开始；否则从上月23号开始
    if now_bj.day >= 23:
        # 当前周期：本月23号 ~ 下月22号
        cur_start = datetime(now_bj.year, now_bj.month, 23)
        if now_bj.month == 12:
            cur_end = datetime(now_bj.year + 1, 1, 22)
        else:
            cur_end = datetime(now_bj.year, now_bj.month + 1, 22)
        # 上一周期：上月23号 ~ 本月22号
        prev_end = datetime(now_bj.year, now_bj.month, 22)
        if now_bj.month == 1:
            prev_start = datetime(now_bj.year - 1, 12, 23)
        else:
            prev_start = datetime(now_bj.year, now_bj.month - 1, 23)
    else:
        # 当前周期：上月23号 ~ 本月22号
        if now_bj.month == 1:
            cur_start = datetime(now_bj.year - 1, 12, 23)
        else:
            cur_start = datetime(now_bj.year, now_bj.month - 1, 23)
        cur_end = datetime(now_bj.year, now_bj.month, 22)
        # 上一周期：上上月23号 ~ 上月22号
        if now_bj.month <= 2:
            prev_start = datetime(now_bj.year - 1, 12, 23) if now_bj.month == 2 else datetime(now_bj.year - 1, 11, 23)
        else:
            prev_start = datetime(now_bj.year, now_bj.month - 2, 23)
        if now_bj.month == 1:
            prev_end = datetime(now_bj.year - 1, 12, 22)
        else:
            prev_end = datetime(now_bj.year, now_bj.month - 1, 22)

    return {
        "current": {"start": cur_start.strftime("%Y-%m-%d"), "end": cur_end.strftime("%Y-%m-%d")},
        "previous": {"start": prev_start.strftime("%Y-%m-%d"), "end": prev_end.strftime("%Y-%m-%d")}
    }


def get_kpi_penalty_data(token):
    """获取KPI类处罚数据，按考核周期汇总，并获取合同和考核详情"""
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8"
        }
        payload = {
            "direction": "DESC",
            "property": "id",
            "fromClientType": "pc",
            "number": 0,
            "size": 9999,
            "dynamicFormCode": "supplier_abnormal_tabul",
            "rules": [
                {"field": "abnormal_category_dk", "option": "EQ", "values": ["KPICLASS"]}
            ],
            "sorts": [{"property": "exception_no", "direction": "DESC"}],
            "specialConditions": []
        }
        resp = _sess().post(KPI_QUERY_URL, json=payload, headers=headers, timeout=20)
        all_items = resp.json().get("content", [])
    except Exception as e:
        print(f"KPI数据查询失败: {e}")
        return {"current_period": {"orders": [], "total_score": 0}, "previous_period": {"orders": [], "total_score": 0}, "periods": {}}

    # 并行获取每条记录的详情（合同信息和考核扣分）
    def fetch_detail(dynamic_form_value_id):
        try:
            url = f"{KPI_DETAIL_URL}/{dynamic_form_value_id}"
            resp = _sess().get(url, headers=headers, timeout=10)
            if resp.status_code == 200 and resp.text:
                detail = resp.json()
                eha = detail.get("data", {}).get("exception_handling_a", {})
                return {
                    "contract": eha.get("related_contract_no_show") or "-",
                    "appraisal_results": eha.get("appraisal_results"),
                    "customer": eha.get("customer") or "-",
                    "send_region": eha.get("send_region_code_show") or "-",
                    "receive_region": eha.get("receive_region_code_show") or "-"
                }
        except:
            pass
        return {"contract": "-", "appraisal_results": None, "customer": "-", "send_region": "-", "receive_region": "-"}

    # 并行获取详情
    detail_map = {}
    with ThreadPoolExecutor(max_workers=25) as executor:
        future_to_id = {}
        for item in all_items:
            dfvid = item.get("dynamic_form_value_id")
            if dfvid:
                future_to_id[executor.submit(fetch_detail, dfvid)] = dfvid
        for future in as_completed(future_to_id):
            dfvid = future_to_id[future]
            try:
                detail_map[dfvid] = future.result()
            except:
                detail_map[dfvid] = {"contract": "-", "appraisal_results": None, "customer": "-", "send_region": "-", "receive_region": "-"}

    periods = get_kpi_cycle_periods()
    cur_start = periods["current"]["start"]
    cur_end = periods["current"]["end"]
    prev_start = periods["previous"]["start"]
    prev_end = periods["previous"]["end"]

    current_orders = []
    previous_orders = []
    current_total_score = 0
    previous_total_score = 0

    for item in all_items:
        eno = item.get("exception_no", "")
        item_date = extract_date_from_exception_no(eno)
        if not item_date:
            continue

        dfvid = item.get("dynamic_form_value_id", "")
        detail = detail_map.get(dfvid, {})

        # 优先使用详情接口的考核扣分，其次用描述正则提取
        appraisal = detail.get("appraisal_results")
        score = float(appraisal) if appraisal is not None else extract_penalty_score(item.get("problem_descripetion", ""))

        order_info = {
            "exception_no": eno,
            "order_no": item.get("oder_number_show") or "-",
            "carrier": item.get("carrier_show") or "-",
            "driver": item.get("driver") or "-",
            "license_plate": item.get("license_plate") or "-",
            "subclass": item.get("abnormal_subclass_dk_show") or "-",
            "description": item.get("problem_descripetion") or "",
            "score": score,
            "date": item_date,
            "contract": detail.get("contract", "-"),
            "customer": detail.get("customer", "-"),
            "send_region": detail.get("send_region", "-"),
            "receive_region": detail.get("receive_region", "-")
        }

        if cur_start <= item_date <= cur_end:
            current_orders.append(order_info)
            current_total_score += score
        elif prev_start <= item_date <= prev_end:
            previous_orders.append(order_info)
            previous_total_score += score

    return {
        "current_period": {
            "orders": current_orders,
            "total_score": round(current_total_score, 1),
            "count": len(current_orders)
        },
        "previous_period": {
            "orders": previous_orders,
            "total_score": round(previous_total_score, 1),
            "count": len(previous_orders)
        },
        "periods": periods
    }


def get_weekly_weight_data(token):
    """获取近7天每天的订单总重量（单次查询7天范围，本地按日期分组，避免7次API调用）"""
    bj_tz = timezone(timedelta(hours=8))
    today_bj = datetime.now(bj_tz)
    date_list = [(today_bj - timedelta(days=i)).date() for i in range(7, 0, -1)]
    labels = [d.strftime("%m-%d") for d in date_list]
    network_ids = [ALL_NETWORKS[n] for n in SELECTED_NETWORKS if n in ALL_NETWORKS]

    # 一次查询覆盖整个7天范围
    first_day = date_list[0]
    last_day = date_list[-1]
    day_start = datetime(first_day.year, first_day.month, first_day.day, 0, 0, 0) - timedelta(hours=8)
    day_end = datetime(last_day.year, last_day.month, last_day.day, 23, 59, 59) - timedelta(hours=8)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "debugFlag": False, "developmentSystemId": None, "direction": "DESC",
        "dynamicFormCode": "stowage_sign_receipt", "fromClientType": "pc",
        "number": 0, "property": "id",
        "rules": [
            {"field": "exe_pur_order_b.order_date", "option": "BTS", "values": [
                day_start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                day_end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            ]},
            {"field": "k_contract_line_a.network", "option": "IN", "values": network_ids}
        ],
        "size": 9999, "sorts": [], "specialConditions": []
    }

    try:
        resp = _sess().post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=30)
        data = resp.json()

        # 按日期分组聚合重量
        daily_weight = {d: 0.0 for d in date_list}
        for o in data.get("content", []):
            order_date_str = (o.get("exe_pur_order_b") or {}).get("order_date", "")
            if order_date_str:
                try:
                    dt = datetime.fromisoformat(order_date_str.replace("Z", "+00:00"))
                    order_date = dt.astimezone(bj_tz).date()
                    if order_date in daily_weight:
                        daily_weight[order_date] += float(o.get("stowage_all_weight", 0) or 0)
                except Exception:
                    pass

        result_data = [round(daily_weight[d], 2) for d in date_list]
    except Exception as e:
        print(f"近7天重量查询失败: {e}")
        result_data = [0] * 7

    print(f"[{datetime.now()}] 近7天重量: {dict(zip(labels, result_data))}")
    return {"labels": labels, "data": result_data}


def query_weekly_orders_by_day(token):
    """查询近7天每天的订单详情"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    # 使用北京时间计算日期
    bj_tz = timezone(timedelta(hours=8))
    today_bj = datetime.now(bj_tz)
    results = []

    # 获取网点ID列表
    network_ids = [ALL_NETWORKS[n] for n in SELECTED_NETWORKS if n in ALL_NETWORKS]

    # 从7天前到昨天（不包括今天）
    for i in range(7, 0, -1):  # 7, 6, 5, 4, 3, 2, 1
        bj_date = (today_bj - timedelta(days=i)).date()
        date_str = bj_date.strftime("%Y-%m-%d")

        # 北京时间当天0点 = UTC前一天16:00
        day_start_utc = datetime(bj_date.year, bj_date.month, bj_date.day, 0, 0, 0) - timedelta(hours=8)
        # 北京时间当天23:59:59 = UTC当天15:59:59
        day_end_utc = datetime(bj_date.year, bj_date.month, bj_date.day, 23, 59, 59) - timedelta(hours=8)

        payload = {
            "debugFlag": False,
            "developmentSystemId": None,
            "direction": "DESC",
            "dynamicFormCode": "stowage_sign_receipt",
            "fromClientType": "pc",
            "number": 0,
            "property": "id",
            "rules": [
                {"field": "exe_pur_order_b.order_date", "option": "BTS", "values": [
                    day_start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                    day_end_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
                ]},
                {"field": "k_contract_line_a.network", "option": "IN", "values": network_ids}
            ],
            "size": 999,
            "sorts": [{"property": "order_date", "direction": "DESC"}],
            "specialConditions": []
        }

        try:
            resp = _sess().post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
            orders = resp.json().get("content", [])
            total_weight = 0
            for o in orders:
                total_weight += float(o.get("stowage_all_weight", 0) or 0)

            results.append({
                "date": date_str,
                "count": len(orders),
                "total_weight": round(total_weight, 2),
                "orders": [{
                    "order_no": o.get("source_order_no", "无"),
                    "stowage_weight": float(o.get("stowage_all_weight", 0) or 0),
                    "receive_name": o.get("receive_name", ""),
                    "province": o.get("province_id_show", ""),
                    "create_time": format_time(o.get("exe_pur_order_b", {}).get("order_date", ""))
                } for o in orders]
            })
        except Exception as e:
            print(f"查询失败 {date_str}: {e}")
            results.append({
                "date": date_str,
                "count": 0,
                "total_weight": 0,
                "orders": []
            })

    return results


# ====================== 数据刷新 ======================
def refresh_all_data():
    """全局刷新 - 使用并行查询优化速度，返回是否成功"""
    global cache_data
    try:
        from time import perf_counter
        t0 = perf_counter()
        print(f"[{datetime.now()}] 开始刷新数据... (省份: {AUTO_REGIONS} | 网点: {SELECTED_NETWORKS})")

        t_login_start = perf_counter()
        token = get_token()
        print(f"  [计时] 登录耗时: {perf_counter() - t_login_start:.2f}s")
        if not token:
            print("登录失败，跳过本次刷新")
            return False

        # 并行执行：统一查询(3合1) + 未签收 + 周趋势 + 发货省份 + 今日订单 + 在途订单
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_unified = executor.submit(get_pending_orders_unified, token)
            future_today_unsigned = executor.submit(get_today_unsigned_data, token)
            future_tomorrow_unsigned = executor.submit(get_tomorrow_unsigned_data, token)
            future_yesterday_unsigned = executor.submit(get_yesterday_unsigned_data, token)
            future_weekly = executor.submit(get_weekly_weight_data, token)
            future_sender = executor.submit(get_sender_region_orders_data, token)
            future_today_orders = executor.submit(get_today_orders_data, token)
            future_intransit = executor.submit(get_intransit_today_count, token)

            # 收集结果
            results = {}
            try:
                t1 = perf_counter()
                manual_query, auto_monitor, region_stats = future_unified.result()
                print(f"  [计时] 统一查询(unified): {perf_counter() - t1:.2f}s")
                results["manual_query"] = manual_query
                results["auto_monitor"] = auto_monitor
                results["region_stats"] = region_stats
            except Exception as e:
                print(f"统一查询失败: {e}")
                results["manual_query"] = {}
                results["auto_monitor"] = {"regions": [], "total_count": 0, "total_weight": 0}
                results["region_stats"] = {}

            # 各 key 的默认回退值（非 dict 类型需明确指定）
            KEY_FALLBACKS = {"intransit_today_count": 0}
            for future, key in [
                (future_today_unsigned, "today_unsigned"),
                (future_tomorrow_unsigned, "tomorrow_unsigned"),
                (future_yesterday_unsigned, "yesterday_unsigned"),
                (future_weekly, "weekly_weight"),
                (future_sender, "sender_region_orders"),
                (future_today_orders, "today_orders"),
                (future_intransit, "intransit_today_count"),
            ]:
                try:
                    t2 = perf_counter()
                    results[key] = future.result()
                    print(f"  [计时] {key}: {perf_counter() - t2:.2f}s")
                except Exception as e:
                    print(f"查询 {key} 失败: {e}")
                    results[key] = KEY_FALLBACKS.get(key, {})

        with cache_lock:
            cache_data.update(results)
            cache_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{datetime.now()}] 数据刷新完成 (总耗时: {perf_counter() - t0:.2f}s)")
        return True
    except Exception as e:
        print(f"刷新数据错误: {e}")
        import traceback
        traceback.print_exc()
        return False


def refresh_pending_orders():
    """仅刷新待配载订单相关数据 - 使用并行查询优化"""
    global cache_data
    print(f"[{datetime.now()}] 开始刷新待配载订单数据...")
    token = get_token()
    if not token:
        print("登录失败，跳过本次刷新")
        return False
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_unified = executor.submit(get_pending_orders_unified, token)
            future_sender = executor.submit(get_sender_region_orders_data, token)
            future_today_orders = executor.submit(get_today_orders_data, token)

            results = {}
            try:
                manual_query, auto_monitor, region_stats = future_unified.result()
                results["manual_query"] = manual_query
                results["auto_monitor"] = auto_monitor
                results["region_stats"] = region_stats
            except Exception as e:
                print(f"统一查询失败: {e}")
                results["manual_query"] = {}
                results["auto_monitor"] = {"regions": [], "total_count": 0, "total_weight": 0}
                results["region_stats"] = {}

            for future, key in [(future_sender, "sender_region_orders"), (future_today_orders, "today_orders")]:
                try:
                    results[key] = future.result()
                except Exception as e:
                    print(f"查询 {key} 失败: {e}")
                    results[key] = {}

        with cache_lock:
            cache_data.update(results)
            cache_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{datetime.now()}] 待配载订单数据刷新完成")
        return True
    except Exception as e:
        print(f"刷新待配载订单数据错误: {e}")
        return False


def background_refresh():
    while True:
        try:
            refresh_all_data()
        except Exception as e:
            print(f"[{datetime.now()}] 后台刷新异常: {e}")
        time.sleep(300)


# ====================== 首页HTML缓存 ======================
_index_html = None


# ====================== API路由 ======================
@app.route('/')
def index():
    global _index_html
    if _index_html is None:
        with open(os.path.join(BASE_DIR, 'index.html'), 'r', encoding='utf-8') as f:
            _index_html = f.read()
    return _index_html


@app.route('/api/dashboard')
def get_dashboard():
    with cache_lock:
        snapshot = dict(cache_data)
        selected_provinces = AUTO_REGIONS
        all_provinces = ALL_PROVINCES
        selected_networks = SELECTED_NETWORKS
        all_network_keys = list(ALL_NETWORKS.keys())
    resp = jsonify({
        "success": True,
        "data": snapshot,
        "selected_provinces": selected_provinces,
        "all_provinces": all_provinces,
        "selected_networks": selected_networks,
        "all_networks": all_network_keys,
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route('/api/refresh', methods=['POST'])
def force_refresh():
    """全局刷新：先应用前端传入的省份和网点配置，再刷新全部数据"""
    global AUTO_REGIONS, SELECTED_NETWORKS
    try:
        data = request.get_json(silent=True)
        if data:
            # 应用省份配置
            provinces = data.get('provinces')
            if isinstance(provinces, list) and len(provinces) > 0:
                AUTO_REGIONS = provinces
                print(f"[{datetime.now()}] 刷新前更新省份配置: {AUTO_REGIONS}")
            # 应用网点配置
            networks = data.get('networks')
            if isinstance(networks, list) and len(networks) > 0:
                SELECTED_NETWORKS = networks
                print(f"[{datetime.now()}] 刷新前更新网点配置: {SELECTED_NETWORKS}")

        success = refresh_all_data()
        if success:
            return jsonify({"success": True, "message": "数据已刷新", "provinces": AUTO_REGIONS, "networks": SELECTED_NETWORKS})
        else:
            return jsonify({"success": False, "message": "登录失败，请检查账号配置"})
    except Exception as e:
        print(f"[ERROR] 全局刷新异常: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"刷新异常: {str(e)}"})


@app.route('/api/health')
def health_check():
    return jsonify({
        "status": "ok",
        "last_update": cache_data.get("last_update")
    })


@app.route('/api/today-unsigned')
def get_today():
    province = request.args.get('province', '')
    with cache_lock:
        data = cache_data.get("today_unsigned", {})
        if province and province != '全部':
            orders = [o for o in data.get("unsigned_orders", []) if o.get("province") == province]
            return jsonify({
                "total_orders": data.get("total_orders", 0),
                "unsigned_count": len(orders),
                "unsigned_orders": orders,
                "provinces": data.get("provinces", [])
            })
        return jsonify(data)


@app.route('/api/tomorrow-unsigned')
def get_tomorrow():
    province = request.args.get('province', '')
    with cache_lock:
        data = cache_data.get("tomorrow_unsigned", {})
        if province and province != '全部':
            orders = [o for o in data.get("unsigned_orders", []) if o.get("province") == province]
            return jsonify({
                "total_orders": data.get("total_orders", 0),
                "unsigned_count": len(orders),
                "unsigned_orders": orders,
                "provinces": data.get("provinces", [])
            })
        return jsonify(data)


@app.route('/api/yesterday-unsigned')
def get_yesterday():
    province = request.args.get('province', '')
    with cache_lock:
        data = cache_data.get("yesterday_unsigned", {})
        if province and province != '全部':
            orders = [o for o in data.get("unsigned_orders", []) if o.get("province") == province]
            return jsonify({
                "total_orders": data.get("total_orders", 0),
                "unsigned_count": len(orders),
                "unsigned_orders": orders,
                "provinces": data.get("provinces", [])
            })
        return jsonify(data)


@app.route('/api/weekly-weight')
def get_weekly_weight():
    """获取近7天订单重量数据"""
    with cache_lock:
        return jsonify(cache_data.get("weekly_weight", {}))


@app.route('/api/kpi-penalty')
def get_kpi_penalty():
    """获取KPI类处罚数据"""
    with cache_lock:
        return jsonify(cache_data.get("kpi_penalty", {}))


def _login_and_refresh(refresh_funcs):
    """通用刷新辅助函数：登录并执行指定的数据获取函数"""
    global cache_data
    token = get_token()
    if not token:
        return {"success": False, "message": "登录失败"}
    try:
        with ThreadPoolExecutor(max_workers=len(refresh_funcs)) as executor:
            future_to_key = {executor.submit(fn, token): key for key, fn in refresh_funcs.items()}
            results = {}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    print(f"刷新 {key} 失败: {e}")
                    results[key] = 0 if key == "intransit_today_count" else {}
        with cache_lock:
            cache_data.update(results)
            cache_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"success": True, "message": "刷新成功"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.route('/api/refresh/overview', methods=['POST'])
def refresh_overview():
    """单独刷新概览卡片 + 今日/明日/昨日未签收"""
    result = _login_and_refresh({
        "auto_monitor": get_auto_monitor_data,
        "today_unsigned": get_today_unsigned_data,
        "tomorrow_unsigned": get_tomorrow_unsigned_data,
        "yesterday_unsigned": get_yesterday_unsigned_data,
    })
    return jsonify(result)


@app.route('/api/refresh/pending-detail', methods=['POST'])
def refresh_pending_detail():
    """单独刷新待配载订单 + 分省统计（使用统一查询优化）"""
    global cache_data
    token = get_token()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})
    try:
        manual_query, auto_monitor, region_stats = get_pending_orders_unified(token)
        with cache_lock:
            cache_data["manual_query"] = manual_query
            cache_data["auto_monitor"] = auto_monitor
            cache_data["region_stats"] = region_stats
            cache_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"success": True, "message": "刷新成功"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/refresh/sender-region', methods=['POST'])
def refresh_sender_region():
    """单独刷新发货省份待配载订单"""
    result = _login_and_refresh({
        "sender_region_orders": get_sender_region_orders_data,
    })
    return jsonify(result)


@app.route('/api/refresh/weekly', methods=['POST'])
def refresh_weekly():
    """单独刷新近7天趋势图"""
    result = _login_and_refresh({
        "weekly_weight": get_weekly_weight_data,
    })
    return jsonify(result)


@app.route('/api/refresh/kpi', methods=['POST'])
def refresh_kpi():
    """单独刷新KPI处罚数据（懒加载，首次或手动触发）"""
    result = _login_and_refresh({
        "kpi_penalty": get_kpi_penalty_data,
    })
    return jsonify(result)


@app.route('/api/refresh/today-orders', methods=['POST'])
def refresh_today_orders():
    """单独刷新今日订单统计"""
    result = _login_and_refresh({
        "today_orders": get_today_orders_data,
    })
    return jsonify(result)


@app.route('/api/intransit-count')
def get_intransit_count():
    """获取在途订单数量（从缓存读取）"""
    networks = list(SELECTED_NETWORKS)
    with cache_lock:
        count = cache_data.get("intransit_today_count", 0)
    return jsonify({
        "success": True,
        "count": count,
        "networks": networks
    })


@app.route('/api/intransit-orders')
def get_intransit_orders():
    """获取所有在途订单详情（按需查询，不走缓存）"""
    network_str = request.args.get('networks', '')
    if network_str:
        network_names = [n.strip() for n in network_str.split(',') if n.strip()]
    else:
        network_names = list(SELECTED_NETWORKS)

    token = get_token()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})

    try:
        data = get_all_intransit_orders(token, network_names)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/tracked-orders', methods=['POST'])
def get_tracked_orders():
    """批量查询重点订单的最新信息（保留备注由前端localStorage管理）"""
    data = request.get_json(silent=True) or {}
    order_nos = data.get('order_nos', [])
    if not order_nos:
        return jsonify({"success": False, "message": "请提供订单号列表"})
    token = get_token()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})

    results = []
    seen = set()
    for order_no in order_nos:
        try:
            items = search_order_by_no(token, order_no)
            for item in items:
                src_no = item.get("source_order_no", "")
                if src_no in seen:
                    continue
                seen.add(src_no)
                exe_pur = item.get("exe_pur_order_b") or {}
                results.append({
                    "order_no": src_no,
                    "transport_order_no": item.get("order_no", ""),
                    "customer_name": exe_pur.get("customer", ""),
                    "customer_group": exe_pur.get("customer_group", ""),
                    "driver_name": item.get("carrier_name", ""),
                    "salesman": item.get("salesman_name", ""),
                    "status": item.get("status_dk_show", ""),
                    "delivery_date": format_time(item.get("delivery_date")),
                    "receive_address": f"{item.get('province_id_show', '') or ''}{item.get('city_id_show', '') or ''}{item.get('district_id_show', '') or ''} {item.get('detailed_address', '') or item.get('receive_address', '') or ''}".strip(),
                    "weight": float(item.get("stowage_all_weight", 0) or 0),
                    "network": (item.get("k_contract_line_a") or {}).get("network_show", ""),
                })
        except Exception as e:
            print(f"查询重点订单 {order_no} 失败: {e}")

    return jsonify({"success": True, "data": results})


def search_order_by_no(token, order_no):
    """通过订单号查询配载单明细"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }
    payload = {
        "direction": "DESC",
        "property": "id",
        "fromClientType": "pc",
        "number": 0,
        "dynamicFormCode": "stowage_sign_receipt",
        "rules": [
            {"field": "source_order_no", "option": "EQ", "values": [order_no]}
        ],
        "size": 100,
        "sorts": [{"property": "order_date", "direction": "DESC"}],
        "specialConditions": []
    }
    resp = _sess().post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=20)
    return resp.json().get("content", [])


@app.route('/api/search-order')
def search_order():
    """根据订单号搜索配载单明细"""
    order_no = request.args.get('order_no', '').strip()
    if not order_no:
        return jsonify({"success": False, "message": "请输入订单号"})
    token = login()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})
    try:
        items = search_order_by_no(token, order_no)
        orders = []
        for o in items:
            orders.append({
                "order_no": o.get("source_order_no", ""),
                "transport_order_no": o.get("order_no", ""),
                "receive_name": o.get("receive_name", ""),
                "receiver_phone": o.get("receiver_phone", ""),
                "contact_person": o.get("receiver_name", "") or o.get("receiver", ""),
                "customer_group": o.get("exe_pur_order_b", {}).get("customer_group", ""),
                "driver": o.get("carrier_name", ""),
                "license_plate": o.get("plate_no", ""),
                "province": o.get("province_id_show", ""),
                "city": o.get("city_id_show", ""),
                "district": o.get("receive_district_code_show", ""),
                "detailed_address": o.get("detailed_address", "") or o.get("receive_address", ""),
                "status": o.get("status_dk_show", ""),
                "stowage_weight": float(o.get("stowage_all_weight", 0) or 0),
                "delivery_date": format_time(o.get("delivery_date")),
                "receive_time": format_time(o.get("receive_time")),
                "order_date": format_time(o.get("exe_pur_order_b", {}).get("order_date", "")),
                "network": o.get("k_contract_line_a", {}).get("network_show", ""),
            })
        return jsonify({"success": True, "data": orders, "total": len(orders)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/weekly-orders', methods=['POST'])
def query_weekly_orders():
    """手动查询近7天订单详情"""
    token = login()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})
    try:
        results = query_weekly_orders_by_day(token)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/today-orders', methods=['POST'])
def query_today_orders():
    """手动查询今日配载订单详情"""
    token = login()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})
    try:
        data = get_today_orders_data(token)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/manual-orders')
def get_manual_orders():
    """待配载订单列表，支持省份筛选"""
    province = request.args.get('province', '')
    with cache_lock:
        data = cache_data.get("manual_query", {})
        orders = data.get("orders", [])
        provinces = sorted(set(o.get("region", "").replace("省", "省") for o in orders if o.get("region")))

        if province and province != '全部':
            filtered_orders = [o for o in orders if province in o.get("region", "")]
        else:
            filtered_orders = orders

        return jsonify({
            "orders": filtered_orders,
            "total_count": data.get("total_count", 0),
            "total_weight": data.get("total_weight", 0),
            "provinces": provinces
        })


@app.route('/api/refresh-manual', methods=['POST'])
def refresh_manual_orders():
    """手动刷新待配载订单数据（使用统一查询优化）"""
    global cache_data
    token = login()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})
    try:
        manual_query, auto_monitor, region_stats = get_pending_orders_unified(token)
        with cache_lock:
            cache_data["manual_query"] = manual_query
            cache_data["auto_monitor"] = auto_monitor
            cache_data["region_stats"] = region_stats
        return jsonify({"success": True, "data": manual_query})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/refresh-pending', methods=['POST'])
def refresh_pending_orders_api():
    """仅刷新待配载订单相关数据（不刷新其他面板数据）"""
    success = refresh_pending_orders()
    if success:
        with cache_lock:
            last_update = cache_data.get("last_update")
        return jsonify({"success": True, "message": "待配载订单数据已刷新", "last_update": last_update})
    else:
        return jsonify({"success": False, "message": "刷新失败"})


@app.route('/api/sender-region-orders')
def get_sender_region_orders():
    """发货省份待配载订单列表，支持发货省份筛选"""
    province = request.args.get('province', '')
    with cache_lock:
        data = cache_data.get("sender_region_orders", {})
        orders = data.get("orders", [])
        provinces = sorted(set(o.get("sender_region", "").replace("省", "") for o in orders if o.get("sender_region")))

        if province and province != '全部':
            filtered_orders = [o for o in orders if province in o.get("sender_region", "")]
        else:
            filtered_orders = orders

        return jsonify({
            "orders": filtered_orders,
            "total_count": data.get("total_count", 0),
            "total_weight": data.get("total_weight", 0),
            "region_details": data.get("region_details", []),
            "provinces": provinces
        })


@app.route('/api/provinces')
def get_provinces():
    """获取当前选中的省份列表"""
    return jsonify({
        "success": True,
        "selected": AUTO_REGIONS,
        "all": ALL_PROVINCES
    })


@app.route('/api/provinces', methods=['POST'])
def set_provinces():
    """设置选中的省份列表"""
    global AUTO_REGIONS
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "message": "无效的请求数据"})
        
        provinces = data.get('provinces', [])
        if isinstance(provinces, list):
            # 允许空数组（全部不选）或至少选择一个
            AUTO_REGIONS = provinces if len(provinces) > 0 else ["湖南", "湖北", "新疆", "河北", "安徽"]
            # 保存到文件
            try:
                config_path = os.path.join(SAVE_DIR, 'province_config.txt')
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(','.join(AUTO_REGIONS))
            except Exception as e:
                print(f"[ERROR] 保存省份配置文件失败: {e}")
            return jsonify({"success": True, "selected": AUTO_REGIONS})
        return jsonify({"success": False, "message": "无效的省份数据格式"})
    except Exception as e:
        print(f"[ERROR] set_provinces异常: {e}")
        return jsonify({"success": False, "message": str(e)})


def load_province_config():
    """加载省份配置"""
    global AUTO_REGIONS
    try:
        config_path = os.path.join(SAVE_DIR, 'province_config.txt')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    AUTO_REGIONS = content.split(',')
    except:
        pass


@app.route('/api/networks')
def get_networks():
    """获取当前选中的网点列表"""
    print(f"[DEBUG] 获取网点配置: selected={SELECTED_NETWORKS}")
    return jsonify({
        "success": True,
        "selected": SELECTED_NETWORKS,
        "all": list(ALL_NETWORKS.keys())
    })


@app.route('/api/networks', methods=['POST'])
def set_networks():
    """设置选中的网点列表"""
    global SELECTED_NETWORKS
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "message": "无效的请求数据"})
        
        networks = data.get('networks', [])
        if isinstance(networks, list):
            # 允许空数组（全部不选）或至少选择一个
            SELECTED_NETWORKS = networks if len(networks) > 0 else list(ALL_NETWORKS.keys())
            # 保存到文件
            try:
                script_dir = SAVE_DIR
                config_path = os.path.join(script_dir, 'network_config.txt')
                with open(config_path, 'w', encoding='utf-8') as f:
                    f.write(','.join(SELECTED_NETWORKS))
            except Exception as e:
                print(f"[ERROR] 保存配置文件失败: {e}")
            return jsonify({"success": True, "selected": SELECTED_NETWORKS})
        return jsonify({"success": False, "message": "无效的网点数据格式"})
    except Exception as e:
        print(f"[ERROR] set_networks异常: {e}")
        return jsonify({"success": False, "message": str(e)})


def load_network_config():
    """加载网点配置"""
    global SELECTED_NETWORKS
    try:
        config_path = os.path.join(SAVE_DIR, 'network_config.txt')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    SELECTED_NETWORKS = content.split(',')
    except:
        pass


# ====================== 订单分析API ======================

# 导出字段配置
EXPORT_SORT_FIELDS = [
    "source_order_no", "k_contract_line_a.network", "order_no", "no", "plate_no", "carrier_name",
    "carrier_phone", "receive_name", "salesman_name", "type_code", "sub_type_code", "status_dk",
    "exe_pur_order_b.order_date", "detailed_address", "receiver_name", "receiver_phone", "expected_arrival_date", "delivery_date",
    "exe_pur_order_b.pur_org_code", "created_date", "start_time", "receive_time", "receiver", "receive_registrant",
    "stowage_all_weight", "exe_pur_order_b.customer", "signed_weight", "exe_pur_order_b.total_weight", "shipping_weight", "refund_weight",
    "exceed_weight", "send_address", "send_name", "exe_delivery_note_b.attachment", "sign_attachment", "arrive_attachment",
    "exe_pur_order_b.purveyor", "receipt_time", "exe_pur_order_b.customer_no", "receive_prescription", "receive_source", "exe_delivery_note_b.is_borrow_sold",
    "exe_delivery_note_b.ship_no", "exe_delivery_note_b.container_no", "order_type_code", "exe_pur_order_b.send_storage_code", "audit_check", "exe_delivery_note_b.yz_flag",
    "exe_pur_order_b.receiver_name", "exe_pur_order_b.receiver_phone", "posting_date", "sign_status", "exe_pur_order_b.customer_group", "send_region_code",
    "receive_region_code", "is_sent_out", "is_post", "exe_pur_order_b.mat_desc", "exe_pur_order_b.change_label_flag", "sent_out_date",
    "the_way_flag", "packaging_num", "material_name", "exe_pur_order_b.sale_org", "exe_pur_order_b.customer_grade", "logistics_time",
    "customer_prescription", "exe_pur_order_b.pick_up_no", "id", "receipt_status", "source_system", "province_id",
    "city_id", "receive_district_code", "exe_delivery_note_b.expect_date_arrival", "car_type", "exe_pur_order_b.transport_mode", "supplier_code",
    "receive_park_code", "unload_arrive", "receive_park_order_no", "car_type", "discharge_flag", "customer_no",
]


def query_stowage_orders(token, rules, size=5000):
    """查询配载单数据"""
    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json;charset=UTF-8"
        }
        payload = {
            "debugFlag": False,
            "developmentSystemId": None,
            "direction": "DESC",
            "dynamicFormCode": "stowage_sign_receipt",
            "fromClientType": "pc",
            "number": 0,
            "property": "id",
            "rules": rules,
            "size": size,
            "sorts": [{"property": "delivery_date", "direction": "ASC"}],
            "specialConditions": []
        }
        resp = _sess().post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=60)
        return resp.json().get("content", [])
    except Exception as e:
        print(f"[ERROR] query_stowage_orders: {e}")
        return []


def parse_stowage_order(order):
    """解析配载单数据"""
    receive_region = order.get("receive_region_code_show", "")
    province = "未知"
    if receive_region and "中国-" in receive_region:
        parts = receive_region.split("-")
        if len(parts) >= 2:
            province = parts[1]
    elif receive_region:
        province = extract_province(receive_region)
    
    return {
        "id": order.get("id"),
        "source_order_no": order.get("source_order_no"),
        "stowage_no": order.get("no"),
        "order_no": order.get("order_no"),
        "status": order.get("status_dk_show"),
        "status_code": order.get("status_dk"),
        "created_date": format_time(order.get("created_date")),
        "delivery_date": format_time(order.get("delivery_date")),
        "receive_time": format_time(order.get("receive_time")),
        "receive_name": order.get("receive_name"),
        "receiver_name": order.get("receiver_name"),
        "receiver_phone": order.get("receiver_phone"),
        "receive_region": receive_region,
        "province": province,
        "detailed_address": order.get("detailed_address"),
        "send_name": order.get("send_name"),
        "send_region": order.get("send_region_code_show"),
        "network": order.get("k_contract_line_a", {}).get("network_show"),
        "network_id": order.get("k_contract_line_a", {}).get("network"),
        "carrier_name": order.get("carrier_name"),
        "plate_no": order.get("plate_no"),
        "carrier_phone": order.get("carrier_phone"),
        "salesman_name": order.get("salesman_name"),
        "stowage_weight": float(order.get("stowage_all_weight", 0) or 0),
        "signed_weight": float(order.get("signed_weight", 0) or 0),
        "logistics_time": order.get("logistics_time_show"),
        "customer_prescription": order.get("customer_prescription_show"),
        "material_name": order.get("material_name"),
        "type_code": order.get("type_code_show"),
        "sub_type_code": order.get("sub_type_code_show"),
        "customer": order.get("exe_pur_order_b", {}).get("customer"),
        "customer_group": order.get("exe_pur_order_b", {}).get("customer_group"),
        "receive_source": order.get("receive_source", ""),
    }


@app.route('/api/order-analysis/query', methods=['POST'])
def api_order_analysis_query():
    """订单分析-查询"""
    try:
        data = request.get_json(silent=True) or {}
        token = get_token()
        if not token:
            return jsonify({"success": False, "message": "登录失败"})

        rules = []

        # 网点筛选（多选）
        networks = data.get('networks')
        if networks and isinstance(networks, list) and len(networks) > 0:
            network_ids = [ALL_NETWORKS[n] for n in networks if n in ALL_NETWORKS]
            if network_ids:
                rules.append({"field": "k_contract_line_a.network", "option": "IN", "values": network_ids})

        # 北京时间→UTC 转换辅助函数
        def bj_date_to_utc(date_str, is_end=False):
            """将前端日期字符串（北京时间）转为UTC时间戳，与项目统一模式一致"""
            date_part = date_str[:10]
            dt = datetime.strptime(date_part, '%Y-%m-%d')
            if is_end:
                bj_utc = datetime(dt.year, dt.month, dt.day, 23, 59, 59) - timedelta(hours=8)
                return bj_utc.strftime('%Y-%m-%dT%H:%M:%S.999Z')
            else:
                bj_utc = datetime(dt.year, dt.month, dt.day, 0, 0, 0) - timedelta(hours=8)
                return bj_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')

        # 创建时间范围（来源单创建时间）
        created_start = data.get('created_start')
        created_end = data.get('created_end')
        if created_start and created_end:
            rules.append({"field": "exe_pur_order_b.order_date", "option": "BTS", "values": [
                bj_date_to_utc(created_start, is_end=False),
                bj_date_to_utc(created_end, is_end=True)
            ]})

        # 签收日期范围
        sign_start = data.get('sign_start')
        sign_end = data.get('sign_end')
        if sign_start and sign_end:
            rules.append({"field": "receive_time", "option": "BTS", "values": [
                bj_date_to_utc(sign_start, is_end=False),
                bj_date_to_utc(sign_end, is_end=True)
            ]})

        # 物流时效 - 注意：物流时效字段不支持API直接筛选，需要前端过滤

        # 配载单状态（多选）
        statuses = data.get('statuses')
        if statuses and isinstance(statuses, list) and len(statuses) > 0:
            status_map = {
                "待配载": "WAITSTOWED",
                "已配载": "WAITDELIVER",
                "已发车确认": "DEPARTRUECONFIR",
                "已签收": "SIGNEDIN",
                "已回单确认": "RECEIPTCONFIR"
            }
            status_vals = [status_map[s] for s in statuses if s in status_map]
            if status_vals:
                rules.append({"field": "status_dk", "option": "IN", "values": status_vals})

        orders_raw = query_stowage_orders(token, rules)
        orders = [parse_stowage_order(o) for o in orders_raw]
        
        return jsonify({
            "success": True,
            "data": orders,
            "total": len(orders),
            "total_weight": round(sum(o["stowage_weight"] for o in orders), 2)
        })
    except Exception as e:
        print(f"[ERROR] api_order_analysis_query: {e}")
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/order-analysis/analyze', methods=['POST'])
def api_order_analysis_analyze():
    """订单分析-统计分析"""
    try:
        data = request.get_json(silent=True) or {}
        orders = data.get('orders', [])
        
        # 按省份统计
        province_stats = {}
        for o in orders:
            p = o.get("province", "未知")
            if p not in province_stats:
                province_stats[p] = {"count": 0, "weight": 0}
            province_stats[p]["count"] += 1
            province_stats[p]["weight"] += o.get("stowage_weight", 0)
        by_province = [{"province": k, "count": v["count"], "weight": round(v["weight"], 2)}
                       for k, v in sorted(province_stats.items(), key=lambda x: x[1]["weight"], reverse=True)]
        
        # 按日期统计
        date_stats = {}
        for o in orders:
            d = (o.get("created_date") or "")[:10]
            if not d:
                d = "未知"
            if d not in date_stats:
                date_stats[d] = {"count": 0, "weight": 0}
            date_stats[d]["count"] += 1
            date_stats[d]["weight"] += o.get("stowage_weight", 0)
        by_date = [{"date": k, "count": v["count"], "weight": round(v["weight"], 2)}
                   for k, v in sorted(date_stats.items())]
        
        # 按网点统计
        network_stats = {}
        for o in orders:
            n = o.get("network", "未知")
            if n not in network_stats:
                network_stats[n] = {"count": 0, "weight": 0}
            network_stats[n]["count"] += 1
            network_stats[n]["weight"] += o.get("stowage_weight", 0)
        by_network = [{"network": k, "count": v["count"], "weight": round(v["weight"], 2)}
                      for k, v in sorted(network_stats.items(), key=lambda x: x[1]["weight"], reverse=True)]
        
        # 按状态统计
        status_stats = {}
        for o in orders:
            s = o.get("status", "未知")
            if s not in status_stats:
                status_stats[s] = {"count": 0, "weight": 0}
            status_stats[s]["count"] += 1
            status_stats[s]["weight"] += o.get("stowage_weight", 0)
        by_status = [{"status": k, "count": v["count"], "weight": round(v["weight"], 2)}
                     for k, v in sorted(status_stats.items(), key=lambda x: x[1]["count"], reverse=True)]
        
        # 按物流时效统计
        logistics_stats = {}
        for o in orders:
            l = o.get("logistics_time") or "未签收"
            if l not in logistics_stats:
                logistics_stats[l] = {"count": 0, "weight": 0}
            logistics_stats[l]["count"] += 1
            logistics_stats[l]["weight"] += o.get("stowage_weight", 0)
        by_logistics = [{"logistics": k, "count": v["count"], "weight": round(v["weight"], 2)}
                        for k, v in sorted(logistics_stats.items(), key=lambda x: x[1]["count"], reverse=True)]
        
        # 每日+省份堆叠数据（按创建时间统计）
        daily_province = {}
        for o in orders:
            d = (o.get("created_date") or "")[:10]
            p = o.get("province", "未知")
            if d not in daily_province:
                daily_province[d] = {}
            if p not in daily_province[d]:
                daily_province[d][p] = 0
            daily_province[d][p] += o.get("stowage_weight", 0)
        
        all_provinces = set()
        for dd in daily_province.values():
            all_provinces.update(dd.keys())
        dates = sorted(daily_province.keys())
        series = []
        for p in sorted(all_provinces):
            data_arr = [round(daily_province.get(d, {}).get(p, 0), 2) for d in dates]
            series.append({"name": p, "data": data_arr})
        
        return jsonify({
            "success": True,
            "data": {
                "by_province": by_province,
                "by_date": by_date,
                "by_network": by_network,
                "by_status": by_status,
                "by_logistics": by_logistics,
                "daily_province": {"dates": dates, "series": series}
            }
        })
    except Exception as e:
        print(f"[ERROR] api_order_analysis_analyze: {e}")
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/order-analysis/export', methods=['POST'])
def api_order_analysis_export():
    """订单分析-导出（使用前端筛选条件）"""
    try:
        data = request.get_json(silent=True) or {}
        token = get_token()
        if not token:
            return jsonify({"success": False, "message": "登录失败"})

        # 构建API筛选规则
        frontend_rules = data.get('rules', [])
        api_rules = []
        for rule in frontend_rules:
            field = rule.get('field', '')
            values = rule.get('values', [])
            if field == 'network':
                network_ids = [ALL_NETWORKS[n] for n in values if n in ALL_NETWORKS]
                if network_ids:
                    api_rules.append({"field": "k_contract_line_a.network", "option": "IN", "values": network_ids})
            elif field == 'created':
                if len(values) == 2:
                    start = datetime.strptime(values[0], '%Y-%m-%d')
                    end = datetime.strptime(values[1], '%Y-%m-%d')
                    utc_start = datetime(start.year, start.month, start.day, 0, 0, 0) - timedelta(hours=8)
                    utc_end = datetime(end.year, end.month, end.day, 23, 59, 59) - timedelta(hours=8)
                    api_rules.append({"field": "exe_pur_order_b.order_date", "option": "BTS", "values": [
                        utc_start.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                        utc_end.strftime('%Y-%m-%dT%H:%M:%S.999Z')
                    ]})
            elif field == 'sign':
                if len(values) == 2:
                    start = datetime.strptime(values[0], '%Y-%m-%d')
                    end = datetime.strptime(values[1], '%Y-%m-%d')
                    utc_start = datetime(start.year, start.month, start.day, 0, 0, 0) - timedelta(hours=8)
                    utc_end = datetime(end.year, end.month, end.day, 23, 59, 59) - timedelta(hours=8)
                    api_rules.append({"field": "receive_time", "option": "BTS", "values": [
                        utc_start.strftime('%Y-%m-%dT%H:%M:%S.000Z'),
                        utc_end.strftime('%Y-%m-%dT%H:%M:%S.999Z')
                    ]})
            elif field == 'status':
                status_map = {
                    "待配载": "WAITSTOWED", "已配载": "WAITDELIVER",
                    "已发车确认": "DEPARTRUECONFIR", "已签收": "SIGNEDIN",
                    "已回单确认": "RECEIPTCONFIR"
                }
                status_vals = [status_map[s] for s in values if s in status_map]
                if status_vals:
                    api_rules.append({"field": "status_dk", "option": "IN", "values": status_vals})

        file_name = data.get('file_name', '订单导出.xlsx')
        # 保存前端传入的自定义文件名，避免被API返回的默认名覆盖
        custom_name = file_name
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "language": "zh_CN",
            "timezone": "UTC+0800"
        }
        
        payload = {
            "direction": "DESC",
            "property": "id",
            "fromClientType": "pc",
            "number": 0,
            "sorts": [],
            "rules": api_rules,
            "size": 5000,
            "specialConditions": [],
            "dynamicFormCode": "stowage_sign_receipt",
            "developmentSystemId": None,
            "debugFlag": False,
            "exportSortFields": EXPORT_SORT_FIELDS
        }
        
        export_url = f"{BASE_URL.replace('/jbl/api', '')}/jbl/api/module-data/receive_management/async-export"
        resp = _sess().post(export_url, json=payload, headers=headers, timeout=30)
        result = resp.json()
        
        if result.get("status") != "success":
            return jsonify({"success": False, "message": result.get("message", "导出失败")})
        
        task_id = result.get("data", {}).get("id")
        
        # 轮询等待完成
        queued_url = f"{BASE_URL.replace('/jbl/api', '')}/jbl/api/queued-task/my/{task_id}"
        for i in range(30):
            time.sleep(2 if i < 3 else 3)
            poll_resp = _sess().get(queued_url, headers=headers, timeout=15)
            if poll_resp.status_code == 200:
                task_data = poll_resp.json()
                if task_data.get("statusEk") == "SUCCEED":
                    output_param = task_data.get("outputParam", {})
                    content_str = output_param.get("content", "")
                    if content_str:
                        content = json.loads(content_str)
                        attach_files = content.get("attachment", {}).get("attachFile", [])
                        if attach_files:
                            file_key = attach_files[0].get("key")
                            api_name = attach_files[0].get("name", "export.xlsx")
                            
                            # 获取授权码
                            auth_url = f"{BASE_URL.replace('/jbl/api', '')}/jbl/api/file/get-temporary-auth-code?key={file_key}"
                            auth_resp = _sess().get(auth_url, headers=headers, timeout=15)
                            auth_code = auth_resp.json().get("temporaryAuthCode")
                            
                            if auth_code:
                                # 下载文件
                                download_url = f"{BASE_URL.replace('/jbl/api', '')}/jbl/api/file/download/{file_key}?authCode={auth_code}"
                                dl_resp = _sess().get(download_url, headers=headers, timeout=60)
                                if dl_resp.status_code == 200:
                                    # 清理文件名中的非法字符
                                    safe_name = re.sub(r'[\\/:*?"<>|]', '_', custom_name)
                                    save_dir = SAVE_DIR
                                    save_path = os.path.join(save_dir, safe_name)
                                    # 文件已存在时加序号避免覆盖冲突
                                    if os.path.exists(save_path):
                                        base, ext = os.path.splitext(safe_name)
                                        for n in range(1, 100):
                                            alt = f"{base} ({n}){ext}"
                                            alt_path = os.path.join(save_dir, alt)
                                            if not os.path.exists(alt_path):
                                                save_path = alt_path
                                                safe_name = alt
                                                break
                                    try:
                                        with open(save_path, "wb") as f:
                                            f.write(dl_resp.content)
                                        return jsonify({"success": True, "filename": safe_name, "path": save_path})
                                    except PermissionError:
                                        return jsonify({"success": False, "message": f"文件被占用无法写入: {safe_name}，请关闭已打开的同名文件后重试"})
        
        return jsonify({"success": False, "message": "导出超时"})
    except Exception as e:
        print(f"[ERROR] api_order_analysis_export: {e}")
        return jsonify({"success": False, "message": str(e)})


# ====================== 启动 ======================
if __name__ == '__main__':
    # 加载省份配置
    load_province_config()
    # 加载网点配置
    load_network_config()
    
    def initial_refresh():
        try:
            refresh_all_data()
        except Exception as e:
            print(f"[{datetime.now()}] 首次刷新异常: {e}")


    def open_browser():
        """延迟2秒后自动打开浏览器"""
        time.sleep(2)
        url = "http://localhost:5000"
        try:
            # 尝试使用系统默认浏览器打开
            if sys.platform == 'win32':
                subprocess.Popen(['start', 'msedge', url], shell=True, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            else:
                webbrowser.open(url)
            print(f"[{datetime.now()}] 已自动打开浏览器")
        except Exception as e:
            print(f"[{datetime.now()}] 打开浏览器失败: {e}")
            print(f"请手动访问: http://localhost:5000")


    initial_thread = threading.Thread(target=initial_refresh, daemon=True)
    initial_thread.start()

    refresh_thread = threading.Thread(target=background_refresh, daemon=True)
    refresh_thread.start()

    # 启动浏览器打开线程
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    print("=" * 50)
    print("   王友小助手 - 已启动")
    print("   正在打开浏览器...")
    print("   按 Ctrl+C 停止服务")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
