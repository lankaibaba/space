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
import webbrowser
import subprocess
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

# 修复 Windows 中文主机名导致的 socket.getfqdn() UnicodeDecodeError
if sys.platform == 'win32':
    import locale
    _orig_getfqdn = socket.getfqdn
    def _getfqdn_fix(name=''):
        try:
            return _orig_getfqdn(name)
        except UnicodeDecodeError:
            enc = locale.getpreferredencoding(False) or 'gbk'
            hostname = socket.gethostname()
            try:
                return hostname.encode('utf-8').decode(enc)
            except:
                return 'localhost'
    socket.getfqdn = _getfqdn_fix

# ====================== 日志系统 ======================
import logging

log_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
log_file = os.path.join(log_dir, 'debug_log.txt')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

def _log_exception(exc_type, exc_value, exc_tb):
    logging.error("未捕获的异常", exc_info=(exc_type, exc_value, exc_tb))
sys.excepthook = _log_exception

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'))
CORS(app)

# ====================== 【核心配置】 ======================
MY_ACCOUNT = "V0011836"
MY_PASSWORD = "Xs123456"
PUBLIC_KEY = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCctQTweXAiaQ3ct5bhj6nyisOQiGmgC/hUdK+QO9I9DudcQSUMxIXvMtpiogB9RWkAUC4b86x7SiGD6aCp7PbTspd5fLf8F6LUIj/BtmktQq7JNsShjAWBxCkE49HIIvPvl9rt8lO7MkgS2vUT04tEYeu/62ltOc3BljJXoPC4pQIDAQAB"

# V4 五省默认配置
DEFAULT_PROVINCES = ['河北', '安徽', '湖南', '湖北', '新疆']

ALL_PROVINCES_LIST = ['河北', '山西', '辽宁', '吉林', '黑龙江', '江苏', '浙江', '安徽', '福建',
                      '江西', '山东', '河南', '湖北', '湖南', '广东', '海南', '四川', '贵州',
                      '云南', '陕西', '甘肃', '青海', '内蒙古', '广西', '西藏', '宁夏', '新疆',
                      '北京', '上海', '天津', '重庆']

# 网点配置
DEFAULT_NETWORKS = ['零担']
ALL_NETWORKS_LIST = ['江南', '非凡', '讯服', '江北', '零担']

# 运行时配置
SELECTED_PROVINCES = DEFAULT_PROVINCES.copy()
SELECTED_NETWORKS = DEFAULT_NETWORKS.copy()

# 网点名 -> ID 映射（运行时查询）
NETWORK_ID_MAP = {}
NETWORK_ID_MAP_LOCK = threading.Lock()

LOGIN_URL = "https://sdm.etransfar.com/jbl/api/login/?_allow_anonymous=true"
QUERY_URL = "https://sdm.etransfar.com/jbl/api/module-data/purchase_order/page"
RECEIPT_QUERY_URL = "https://sdm.etransfar.com/jbl/api/module-data/stowage_sign_receipt/page"
NETWORK_QUERY_URL = "https://sdm.etransfar.com/jbl/api/module-data/contract_line/page"

# 全局缓存数据
cache_data = {
    "manual_query": {},
    "auto_monitor": {},
    "region_stats": {},
    "today_unsigned": {},
    "tomorrow_unsigned": {},
    "sender_region_orders": {},
    "weekly_weight": {},
    "last_update": None
}
cache_lock = threading.Lock()


# ====================== 工具函数 ======================
def rsa_encrypt(password):
    pub_key = f"-----BEGIN PUBLIC KEY-----\n{PUBLIC_KEY}\n-----END PUBLIC KEY-----"
    rsa_key = RSA.importKey(pub_key)
    cipher = PKCS1_v1_5.new(rsa_key)
    encrypted = cipher.encrypt(password.encode())
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
        resp = requests.post(LOGIN_URL, json=payload, headers=headers, timeout=15)
        data = resp.json()
        if data.get("status") == "login":
            return data["token"]
        return None
    except:
        return None


def format_time(time_str):
    if not time_str:
        return None
    try:
        dt_utc = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        dt_bj = dt_utc.astimezone(timezone(timedelta(hours=8)))
        return dt_bj.strftime("%Y-%m-%d %H:%M:%S")
    except:
        return time_str


def extract_province(city_str):
    if not city_str:
        return "未知"
    if "省" in city_str:
        return city_str.split("省")[0] + "省"
    for p in ALL_PROVINCES_LIST:
        if p in city_str:
            return p
    return city_str[:6] if len(city_str) > 6 else city_str


# ====================== 网点映射查询 ======================
def fetch_network_mapping(token):
    """查询网点名称到ID的映射"""
    global NETWORK_ID_MAP
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
            "dynamicFormCode": "contract_line",
            "rules": [],
            "size": 999,
            "sorts": [],
            "specialConditions": []
        }
        resp = requests.post(NETWORK_QUERY_URL, json=payload, headers=headers, timeout=15)
        data = resp.json()
        records = data.get("content", [])
        
        new_map = {}
        for r in records:
            network_show = r.get("network_show", "")
            network_id = r.get("network", "")
            if network_show and network_id:
                # 提取简短名称（如 "纺化业务——零担" → "零担"）
                short_name = network_show
                if "——" in short_name:
                    short_name = short_name.split("——")[-1]
                new_map[short_name] = network_id
                new_map[network_show] = network_id  # 也保留完整名称映射
        
        with NETWORK_ID_MAP_LOCK:
            NETWORK_ID_MAP = new_map
        
        log.info(f"网点映射已更新: {list(NETWORK_ID_MAP.keys())}")
        return new_map
    except Exception as e:
        log.error(f"查询网点映射失败: {e}")
        return {}


# ====================== 数据获取函数 ======================
def query_orders(token, region):
    """按收货省份查询待配载订单"""
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
            "size": 9999999,
            "sorts": [
                {"property": "required_arrival_date", "direction": "ASC"},
                {"property": "receive_region_code", "direction": "ASC"}
            ],
            "specialConditions": []
        }
        resp = requests.post(QUERY_URL, json=payload, headers=headers, timeout=20)
        return resp.json().get("content", [])
    except:
        return []


def get_manual_query_data(token):
    all_orders = []
    provinces = SELECTED_PROVINCES.copy()
    for region in provinces:
        orders = query_orders(token, region)
        for o in orders:
            all_orders.append({
                "order_no": o.get("source_order_no", "无"),
                "region": o.get("receive_region_code_show", "无"),
                "weight": float(o.get("total_weight", 0)),
                "warehouse": o.get("all_send_storage_code_show", "无"),
                "order_date": format_time(o.get("order_date")),
                "urgent_flag_custom": o.get("urgent_flag_custom", "无"),
                "the_way_flag_custom": o.get("the_way_flag_custom", "无")
            })
    total_weight = sum(o["weight"] for o in all_orders)
    return {
        "orders": all_orders,
        "total_count": len(all_orders),
        "total_weight": round(total_weight, 2)
    }


def get_auto_monitor_data(token):
    region_stats = []
    provinces = SELECTED_PROVINCES.copy()
    for region in provinces:
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
    provinces = SELECTED_PROVINCES.copy()
    all_orders = []
    region_details = []
    for region in provinces:
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
                "weight": float(o.get("total_weight", 0)),
                "warehouse": o.get("all_send_storage_code_show", "无"),
                "order_date": format_time(o.get("order_date")),
                "urgent_flag_custom": o.get("urgent_flag_custom", "无"),
                "the_way_flag_custom": o.get("the_way_flag_custom", "无")
            })
    total_count = sum(r["count"] for r in region_details)
    total_weight = sum(r["weight"] for r in region_details)
    return {
        "region_details": region_details,
        "orders": all_orders,
        "total_count": total_count,
        "total_weight": round(total_weight, 2)
    }


def get_unsigned_orders_data(token, is_tomorrow=False):
    """查询今日/明日未签收订单（按网点+五省筛选）"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    bj_tz = timezone(timedelta(hours=8))
    now_bj = datetime.now(bj_tz)

    if is_tomorrow:
        tomorrow_bj = (now_bj + timedelta(days=1)).date()
        start = datetime(tomorrow_bj.year, tomorrow_bj.month, tomorrow_bj.day, 0, 0, 0) - timedelta(hours=8)
        end = datetime(tomorrow_bj.year, tomorrow_bj.month, tomorrow_bj.day, 23, 59, 59) - timedelta(hours=8)
    else:
        today_bj = now_bj.date()
        start = datetime(today_bj.year, today_bj.month, today_bj.day, 0, 0, 0) - timedelta(hours=8)
        end = datetime(today_bj.year, today_bj.month, today_bj.day, 23, 59, 59) - timedelta(hours=8)

    # 获取选中的网点ID列表
    with NETWORK_ID_MAP_LOCK:
        id_map = NETWORK_ID_MAP.copy()
    
    network_ids = []
    for net_name in SELECTED_NETWORKS:
        if net_name in id_map:
            network_ids.append(id_map[net_name])
        else:
            # 备选：遍历映射找匹配
            for k, v in id_map.items():
                if net_name in k:
                    network_ids.append(v)
                    break

    log.debug(f"查询未签收订单 - 选中网点: {SELECTED_NETWORKS}, 网点IDs: {network_ids}")

    # 构建查询规则
    rules = [
        {"field": "delivery_date", "option": "BTS", "values": [
            start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            end.strftime("%Y-%m-%dT%H:%M:%S.999Z")
        ]}
    ]
    
    if network_ids:
        rules.append({"field": "k_contract_line_a.network", "option": "IN", "values": network_ids})

    payload = {
        "debugFlag": False,
        "developmentSystemId": None,
        "direction": "DESC",
        "dynamicFormCode": "stowage_sign_receipt",
        "fromClientType": "pc",
        "number": 0,
        "property": "id",
        "rules": rules,
        "size": 2000,
        "sorts": [{"property": "receive_time", "direction": "DESC"}],
        "specialConditions": []
    }

    log.debug(f"查询payload rules: {rules}")

    try:
        resp = requests.post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
        resp_data = resp.json()
        orders = resp_data.get("content", [])
    except Exception as e:
        log.error(f"查询未签收订单失败: {e}")
        orders = []

    log.debug(f"API返回订单数: {len(orders)}")

    # 五省筛选
    target_provinces = SELECTED_PROVINCES.copy()
    unsigned_orders = []
    all_provinces = set()
    for item in orders:
        status = item.get("status_dk_show", "")
        if status != "已签收":
            province = item.get("province_id_show", "")
            city = item.get("city_id_show", "")
            district = item.get("district_id_show", "")
            street = item.get("street_id_show", "")
            full_address = item.get("receive_address", "")

            if not province and city:
                province = extract_province(city)

            # 五省筛选
            province_match = False
            for tp in target_provinces:
                if tp in (province or "") or tp in (city or "") or tp in (full_address or ""):
                    province_match = True
                    break
            if not province_match:
                continue

            if province:
                all_provinces.add(province)

            # 从 content 中读取 delivery_date 作为需求到货时间
            content = item.get("content", {}) or {}
            delivery_date = content.get("delivery_date", "")

            unsigned_orders.append({
                "order_no": item.get("source_order_no", "无"),
                "receive_name": item.get("receive_name", ""),
                "receiver_phone": item.get("receiver_phone", ""),
                "province": province,
                "city": city,
                "district": district,
                "street": street,
                "detailed_address": item.get("detailed_address", full_address),
                "address": city + (district if district else ""),
                "status": status,
                "receive_time": format_time(item.get("receive_time")),
                "signed_weight": float(item.get("stowage_all_weight", 0) or 0),
                "expected_arrival_date": format_time(delivery_date) if delivery_date else None
            })

    log.debug(f"返回订单省份分布: {dict(sorted(((p, sum(1 for o in unsigned_orders if o['province'] == p)) for p in set(o['province'] for o in unsigned_orders))))}")

    return {
        "total_orders": len(orders),
        "unsigned_count": len(unsigned_orders),
        "unsigned_orders": unsigned_orders,
        "provinces": sorted(list(all_provinces))
    }


def get_today_unsigned_data(token):
    return get_unsigned_orders_data(token, is_tomorrow=False)


def get_tomorrow_unsigned_data(token):
    return get_unsigned_orders_data(token, is_tomorrow=True)


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
            "size": 9999999,
            "sorts": [
                {"property": "order_date", "direction": "DESC"}
            ],
            "specialConditions": []
        }
        resp = requests.post(QUERY_URL, json=payload, headers=headers, timeout=20)
        return resp.json().get("content", [])
    except:
        return []


def get_sender_region_orders_data(token):
    """获取发货地址为配置省份的待配载订单"""
    provinces = SELECTED_PROVINCES.copy()
    all_orders = []
    region_details = []

    for region in provinces:
        orders = query_orders_by_sender_region(token, region)
        total_weight = sum(float(o.get("total_weight", 0)) for o in orders)
        region_details.append({
            "region": region,
            "count": len(orders),
            "weight": round(total_weight, 2)
        })
        for o in orders:
            all_orders.append({
                "order_no": o.get("source_order_no", "无"),
                "sender_region": o.get("send_region_code_show", "无"),
                "receive_region": o.get("receive_region_code_show", "无"),
                "weight": float(o.get("total_weight", 0)),
                "stowage_weight": float(o.get("stowage_all_weight", 0) or 0),
                "warehouse": o.get("all_send_storage_code_show", "无"),
                "create_time": format_time(o.get("order_date")),
                "on_the_way": o.get("the_way_flag_custom", "无")
            })

    total_count = sum(r["count"] for r in region_details)
    total_weight = sum(r["weight"] for r in region_details)

    return {
        "region_details": region_details,
        "orders": all_orders,
        "total_count": total_count,
        "total_weight": round(total_weight, 2)
    }


def get_weekly_weight_data(token):
    """获取近7天每天的订单总重量数据（按网点筛选）"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    bj_tz = timezone(timedelta(hours=8))
    today_bj = datetime.now(bj_tz)

    date_list = []
    for i in range(7, 0, -1):
        bj_date = (today_bj - timedelta(days=i)).date()
        date_list.append(bj_date)

    labels = [d.strftime("%m-%d") for d in date_list]
    log.info(f"近7天日期范围: {labels[0]} 至 {labels[-1]}")

    # 获取选中的网点ID列表
    with NETWORK_ID_MAP_LOCK:
        id_map = NETWORK_ID_MAP.copy()
    
    network_ids = []
    for net_name in SELECTED_NETWORKS:
        if net_name in id_map:
            network_ids.append(id_map[net_name])
        else:
            for k, v in id_map.items():
                if net_name in k:
                    network_ids.append(v)
                    break

    data = []
    for bj_date in date_list:
        day_start_utc = datetime(bj_date.year, bj_date.month, bj_date.day, 0, 0, 0) - timedelta(hours=8)
        day_end_utc = datetime(bj_date.year, bj_date.month, bj_date.day, 23, 59, 59) - timedelta(hours=8)

        rules = [
            {"field": "delivery_date", "option": "BTS", "values": [
                day_start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                day_end_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            ]}
        ]
        if network_ids:
            rules.append({"field": "k_contract_line_a.network", "option": "IN", "values": network_ids})

        payload = {
            "debugFlag": False,
            "developmentSystemId": None,
            "direction": "DESC",
            "dynamicFormCode": "stowage_sign_receipt",
            "fromClientType": "pc",
            "number": 0,
            "property": "id",
            "rules": rules,
            "size": 999,
            "sorts": [],
            "specialConditions": []
        }

        try:
            resp = requests.post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
            resp_data = resp.json()
            total_sum_data = resp_data.get("totalSumData", {})
            daily_weight = 0
            if total_sum_data:
                daily_weight = float(total_sum_data.get("stowage_all_weight", 0) or 0)
            data.append(round(daily_weight, 2))
            log.info(f"{bj_date.strftime('%m-%d')}: {daily_weight} kg")
        except Exception as e:
            log.warning(f"查询失败 {bj_date.strftime('%m-%d')}: {e}")
            data.append(0)

    return {
        "labels": labels,
        "data": data
    }


def query_weekly_orders_by_day(token):
    """查询近7天每天的订单详情（按网点筛选）"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    bj_tz = timezone(timedelta(hours=8))
    today_bj = datetime.now(bj_tz)
    results = []

    # 获取选中的网点ID列表
    with NETWORK_ID_MAP_LOCK:
        id_map = NETWORK_ID_MAP.copy()
    
    network_ids = []
    for net_name in SELECTED_NETWORKS:
        if net_name in id_map:
            network_ids.append(id_map[net_name])
        else:
            for k, v in id_map.items():
                if net_name in k:
                    network_ids.append(v)
                    break

    for i in range(7, 0, -1):
        bj_date = (today_bj - timedelta(days=i)).date()
        date_str = bj_date.strftime("%Y-%m-%d")

        day_start_utc = datetime(bj_date.year, bj_date.month, bj_date.day, 0, 0, 0) - timedelta(hours=8)
        day_end_utc = datetime(bj_date.year, bj_date.month, bj_date.day, 23, 59, 59) - timedelta(hours=8)

        rules = [
            {"field": "delivery_date", "option": "BTS", "values": [
                day_start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                day_end_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            ]}
        ]
        if network_ids:
            rules.append({"field": "k_contract_line_a.network", "option": "IN", "values": network_ids})

        payload = {
            "debugFlag": False,
            "developmentSystemId": None,
            "direction": "DESC",
            "dynamicFormCode": "stowage_sign_receipt",
            "fromClientType": "pc",
            "number": 0,
            "property": "id",
            "rules": rules,
            "size": 999,
            "sorts": [{"property": "receive_time", "direction": "DESC"}],
            "specialConditions": []
        }

        try:
            resp = requests.post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
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
                    "create_time": format_time(o.get("receive_time", ""))
                } for o in orders]
            })
        except Exception as e:
            log.warning(f"查询失败 {date_str}: {e}")
            results.append({
                "date": date_str,
                "count": 0,
                "total_weight": 0,
                "orders": []
            })

    return results


# ====================== 数据刷新 ======================
def refresh_all_data():
    """全局刷新 - 使用并行查询优化速度"""
    global cache_data
    log.info("开始刷新数据...")
    log.debug(f"当前筛选省份: {SELECTED_PROVINCES}, 网点: {SELECTED_NETWORKS}")
    token = login()
    if not token:
        log.error("登录失败，跳过本次刷新")
        return
    
    # 每次刷新时更新网点映射
    fetch_network_mapping(token)
    
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            future_to_key = {
                executor.submit(get_manual_query_data, token): "manual_query",
                executor.submit(get_auto_monitor_data, token): "auto_monitor",
                executor.submit(get_region_stats_data, token): "region_stats",
                executor.submit(get_today_unsigned_data, token): "today_unsigned",
                executor.submit(get_tomorrow_unsigned_data, token): "tomorrow_unsigned",
                executor.submit(get_weekly_weight_data, token): "weekly_weight",
                executor.submit(get_sender_region_orders_data, token): "sender_region_orders",
            }

            results = {}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    result_data = future.result()
                    results[key] = result_data
                    if key in ['today_unsigned', 'tomorrow_unsigned']:
                        log.debug(f"{key} 查询结果: {len(result_data.get('unsigned_orders', []))} 条订单")
                except Exception as e:
                    log.error(f"查询 {key} 失败: {e}")
                    results[key] = {} if key != "auto_monitor" else {"regions": [], "total_count": 0, "total_weight": 0}

        with cache_lock:
            cache_data.update(results)
            cache_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log.info("数据刷新完成")
    except Exception as e:
        log.error(f"刷新数据错误: {e}")


def refresh_pending_orders():
    """仅刷新待配载订单相关数据"""
    global cache_data
    log.info("开始刷新待配载订单数据...")
    token = login()
    if not token:
        log.error("登录失败，跳过本次刷新")
        return False
    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_key = {
                executor.submit(get_manual_query_data, token): "manual_query",
                executor.submit(get_auto_monitor_data, token): "auto_monitor",
                executor.submit(get_region_stats_data, token): "region_stats",
                executor.submit(get_sender_region_orders_data, token): "sender_region_orders",
            }

            results = {}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    log.error(f"查询 {key} 失败: {e}")
                    results[key] = {"regions": [], "total_count": 0, "total_weight": 0} if key == "auto_monitor" else {}

        with cache_lock:
            cache_data.update(results)
            cache_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log.info("待配载订单数据刷新完成")
        return True
    except Exception as e:
        log.error(f"刷新待配载订单数据错误: {e}")
        return False


def background_refresh():
    while True:
        try:
            refresh_all_data()
        except Exception as e:
            log.error(f"后台刷新异常: {e}")
        time.sleep(300)


# ====================== API路由 ======================
@app.route('/')
def index():
    index_path = os.path.join(BASE_DIR, 'static', 'index.html')
    with open(index_path, 'r', encoding='utf-8') as f:
        return f.read()


@app.route('/api/dashboard')
def get_dashboard():
    with cache_lock:
        return jsonify({
            "success": True,
            "data": cache_data,
            "selected_provinces": SELECTED_PROVINCES,
            "all_provinces": ALL_PROVINCES_LIST,
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })


@app.route('/api/refresh', methods=['POST'])
def force_refresh():
    refresh_all_data()
    return jsonify({"success": True, "message": "数据已刷新"})


@app.route('/api/health')
def health_check():
    return jsonify({
        "status": "ok",
        "last_update": cache_data.get("last_update")
    })


# ====================== 省份配置 API ======================
@app.route('/api/provinces')
def get_provinces():
    """获取省份配置"""
    return jsonify({
        "success": True,
        "selected": SELECTED_PROVINCES,
        "all": ALL_PROVINCES_LIST
    })


@app.route('/api/provinces', methods=['POST'])
def set_provinces():
    """设置选中的省份列表"""
    global SELECTED_PROVINCES
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "message": "无效的请求数据"})
        
        provinces = data.get('provinces', [])
        if isinstance(provinces, list) and len(provinces) > 0:
            SELECTED_PROVINCES = provinces
            save_province_config()
            return jsonify({"success": True, "selected": SELECTED_PROVINCES})
        return jsonify({"success": False, "message": "无效的省份数据格式"})
    except Exception as e:
        log.error(f"set_provinces异常: {e}")
        return jsonify({"success": False, "message": str(e)})


# ====================== 网点配置 API ======================
@app.route('/api/networks')
def get_networks():
    """获取网点配置"""
    return jsonify({
        "success": True,
        "selected": SELECTED_NETWORKS,
        "all": ALL_NETWORKS_LIST
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
        if isinstance(networks, list) and len(networks) > 0:
            SELECTED_NETWORKS = networks
            save_network_config()
            return jsonify({"success": True, "selected": SELECTED_NETWORKS})
        return jsonify({"success": False, "message": "无效的网点数据格式"})
    except Exception as e:
        log.error(f"set_networks异常: {e}")
        return jsonify({"success": False, "message": str(e)})


# ====================== 其他数据 API ======================
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


@app.route('/api/weekly-weight')
def get_weekly_weight():
    with cache_lock:
        return jsonify(cache_data.get("weekly_weight", {}))


@app.route('/api/weekly-orders', methods=['POST'])
def query_weekly_orders():
    token = login()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})
    try:
        results = query_weekly_orders_by_day(token)
        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/manual-orders')
def get_manual_orders():
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
    token = login()
    if not token:
        return jsonify({"success": False, "message": "登录失败"})
    try:
        data = get_manual_query_data(token)
        with cache_lock:
            cache_data["manual_query"] = data
            cache_data["auto_monitor"] = get_auto_monitor_data(token)
            cache_data["region_stats"] = get_region_stats_data(token)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/refresh-pending', methods=['POST'])
def refresh_pending_orders_api():
    success = refresh_pending_orders()
    if success:
        with cache_lock:
            last_update = cache_data.get("last_update")
        return jsonify({"success": True, "message": "待配载订单数据已刷新", "last_update": last_update})
    else:
        return jsonify({"success": False, "message": "刷新失败"})


@app.route('/api/sender-region-orders')
def get_sender_region_orders():
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


# ====================== 配置持久化 ======================
def save_province_config():
    """保存省份配置到文件"""
    try:
        config_path = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__)), 'province_config.txt')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(','.join(SELECTED_PROVINCES))
        log.info(f"省份配置已保存: {SELECTED_PROVINCES}")
    except Exception as e:
        log.error(f"保存省份配置失败: {e}")


def save_network_config():
    """保存网点配置到文件"""
    try:
        config_path = os.path.dirname(os.path.abspath(sys.argv[0])) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(config_path, 'network_config.txt')
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(','.join(SELECTED_NETWORKS))
        log.info(f"网点配置已保存: {SELECTED_NETWORKS}")
    except Exception as e:
        log.error(f"保存网点配置失败: {e}")


def load_province_config():
    """加载省份配置"""
    global SELECTED_PROVINCES
    try:
        config_path = os.path.dirname(os.path.abspath(sys.argv[0])) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(config_path, 'province_config.txt')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    loaded = content.split(',')
                    # 只保留在五省范围内的省份
                    valid = [p for p in loaded if p in DEFAULT_PROVINCES]
                    if valid:
                        SELECTED_PROVINCES = valid
                        log.info(f"已加载省份配置: {SELECTED_PROVINCES}")
    except:
        pass


def load_network_config():
    """加载网点配置"""
    global SELECTED_NETWORKS
    try:
        config_path = os.path.dirname(os.path.abspath(sys.argv[0])) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(config_path, 'network_config.txt')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    loaded = content.split(',')
                    if loaded:
                        SELECTED_NETWORKS = loaded
                        log.info(f"已加载网点配置: {SELECTED_NETWORKS}")
    except:
        pass


# ====================== 启动 ======================
if __name__ == '__main__':
    load_province_config()
    load_network_config()

    def initial_refresh():
        try:
            refresh_all_data()
        except Exception as e:
            log.error(f"首次刷新异常: {e}")

    def open_browser():
        time.sleep(2)
        url = "http://localhost:5000"
        try:
            if sys.platform == 'win32':
                edge_paths = [
                    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
                ]
                edge_found = False
                for edge_path in edge_paths:
                    if os.path.exists(edge_path):
                        subprocess.Popen([edge_path, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        edge_found = True
                        break
                if not edge_found:
                    try:
                        subprocess.Popen(['start', 'msedge', url], shell=True, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                        edge_found = True
                    except:
                        pass
                if not edge_found:
                    webbrowser.open(url)
            else:
                webbrowser.open(url)
            log.info("已自动打开浏览器")
        except Exception as e:
            log.error(f"打开浏览器失败: {e}")
            log.info(f"请手动访问: http://localhost:5000")

    initial_thread = threading.Thread(target=initial_refresh, daemon=True)
    initial_thread.start()

    refresh_thread = threading.Thread(target=background_refresh, daemon=True)
    refresh_thread.start()

    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()

    log.info("=" * 50)
    log.info("   订单可视化看板 V4 - 已启动")
    log.info("   五省筛选: 河北、安徽、湖南、湖北、新疆")
    log.info("   正在打开浏览器...")
    log.info("   http://localhost:5000")
    log.info("   日志文件: debug_log.txt")
    log.info("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
