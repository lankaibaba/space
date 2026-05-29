import sys, os, re

# 打包后静默运行，不弹命令行窗口
if getattr(sys, 'frozen', False):
    sys.stdout = open(os.devnull, 'w')
    sys.stderr = open(os.devnull, 'w')

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
from concurrent.futures import ThreadPoolExecutor, as_completed

# 判断是否为打包后的程序
if getattr(sys, 'frozen', False):
    # 打包后的程序
    BASE_DIR = sys._MEIPASS
else:
    # 开发环境
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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
    "零担": "740441957821714432"
}
# 默认选中的网点
SELECTED_NETWORKS = ["零担"]

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
    "sender_region_orders": {},
    "today_orders": {},
    "kpi_penalty": {},
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
    for p in ["河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北",
              "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏",
              "新疆"]:
        if p in city_str:
            return p
    return city_str[:6] if len(city_str) > 6 else city_str


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
    for region in AUTO_REGIONS:
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


def get_unsigned_orders_data(token, is_tomorrow=False):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    # 使用北京时间计算日期
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
        resp = requests.post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
        orders = resp.json().get("content", [])
    except:
        orders = []

    unsigned_orders = []
    all_provinces = set()
    for item in orders:
        status = item.get("status_dk_show", "")
        if status != "已签收":
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
    regions = AUTO_REGIONS.copy()  # 使用配置的省份
    all_orders = []
    region_details = []
    
    for region in regions:
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
                "sender_region": o.get("send_region_code_show", "无"),  # 发货地址
                "receive_region": o.get("receive_region_code_show", "无"),  # 收货地址
                "weight": float(o.get("total_weight", 0)),
                "stowage_weight": float(o.get("stowage_all_weight", 0) or 0),  # 配载重量
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
        resp = requests.post(RECEIPT_QUERY_URL, json=payload, headers=headers, timeout=15)
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
        resp = requests.post(KPI_QUERY_URL, json=payload, headers=headers, timeout=20)
        all_items = resp.json().get("content", [])
    except Exception as e:
        print(f"KPI数据查询失败: {e}")
        return {"current_period": {"orders": [], "total_score": 0}, "previous_period": {"orders": [], "total_score": 0}, "periods": {}}

    # 并行获取每条记录的详情（合同信息和考核扣分）
    def fetch_detail(dynamic_form_value_id):
        try:
            url = f"{KPI_DETAIL_URL}/{dynamic_form_value_id}"
            resp = requests.get(url, headers=headers, timeout=10)
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
    """获取近7天每天的订单总重量数据（不包括今天）"""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0"
    }

    # 使用北京时间计算日期
    bj_tz = timezone(timedelta(hours=8))
    today_bj = datetime.now(bj_tz)

    # 近7天数据 - 从7天前到昨天（不包括今天）
    # 例如：今天是4月17日，则显示4月10日到4月16日（从左到右日期递增）
    date_list = []
    for i in range(7, 0, -1):  # 从7倒序到1：7天前, 6天前, ..., 昨天
        bj_date = (today_bj - timedelta(days=i)).date()
        date_list.append(bj_date)

    labels = [d.strftime("%m-%d") for d in date_list]
    print(f"[{datetime.now()}] 近7天日期范围: {labels[0]} 至 {labels[-1]}")

    # 获取网点ID列表
    network_ids = [ALL_NETWORKS[n] for n in SELECTED_NETWORKS if n in ALL_NETWORKS]

    # 每天分别查询
    data = []
    for bj_date in date_list:

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
            print(f"[{datetime.now()}] {bj_date.strftime('%m-%d')}: {daily_weight} kg")
        except Exception as e:
            print(f"查询失败 {bj_date.strftime('%m-%d')}: {e}")
            data.append(0)

    return {
        "labels": labels,
        "data": data
    }


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
    print(f"[{datetime.now()}] 开始刷新数据... (省份: {AUTO_REGIONS} | 网点: {SELECTED_NETWORKS})")
    token = login()
    if not token:
        print("登录失败，跳过本次刷新")
        return False
    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            # 并行提交所有查询任务（KPI单独刷新，不在此处加载）
            future_to_key = {
                executor.submit(get_manual_query_data, token): "manual_query",
                executor.submit(get_auto_monitor_data, token): "auto_monitor",
                executor.submit(get_region_stats_data, token): "region_stats",
                executor.submit(get_today_unsigned_data, token): "today_unsigned",
                executor.submit(get_tomorrow_unsigned_data, token): "tomorrow_unsigned",
                executor.submit(get_weekly_weight_data, token): "weekly_weight",
                executor.submit(get_sender_region_orders_data, token): "sender_region_orders",
                executor.submit(get_today_orders_data, token): "today_orders",
            }

            # 收集结果
            results = {}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    print(f"查询 {key} 失败: {e}")
                    results[key] = {} if key != "auto_monitor" else {"regions": [], "total_count": 0, "total_weight": 0}

        with cache_lock:
            cache_data.update(results)
            cache_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{datetime.now()}] 数据刷新完成")
        return True
    except Exception as e:
        print(f"刷新数据错误: {e}")
        return False


def refresh_pending_orders():
    """仅刷新待配载订单相关数据 - 使用并行查询优化"""
    global cache_data
    print(f"[{datetime.now()}] 开始刷新待配载订单数据...")
    token = login()
    if not token:
        print("登录失败，跳过本次刷新")
        return False
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            # 并行刷新待配载相关数据
            future_to_key = {
                executor.submit(get_manual_query_data, token): "manual_query",
                executor.submit(get_auto_monitor_data, token): "auto_monitor",
                executor.submit(get_region_stats_data, token): "region_stats",
                executor.submit(get_sender_region_orders_data, token): "sender_region_orders",
                executor.submit(get_today_orders_data, token): "today_orders",
            }

            results = {}
            for future in as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results[key] = future.result()
                except Exception as e:
                    print(f"查询 {key} 失败: {e}")
                    results[key] = {"regions": [], "total_count": 0, "total_weight": 0} if key == "auto_monitor" else {}

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


# ====================== API路由 ======================
@app.route('/')
def index():
    index_path = os.path.join(BASE_DIR, 'index.html')
    with open(index_path, 'r', encoding='utf-8') as f:
        return f.read()


@app.route('/api/dashboard')
def get_dashboard():
    with cache_lock:
        return jsonify({
            "success": True,
            "data": cache_data,
            "selected_provinces": AUTO_REGIONS,
            "all_provinces": ALL_PROVINCES,
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })


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
    except Exception as e:
        print(f"[ERROR] 解析refresh请求参数失败: {e}")

    success = refresh_all_data()
    if success:
        return jsonify({"success": True, "message": "数据已刷新", "provinces": AUTO_REGIONS, "networks": SELECTED_NETWORKS})
    else:
        return jsonify({"success": False, "message": "登录失败，请检查账号配置"})


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
    token = login()
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
                    results[key] = {}
        with cache_lock:
            cache_data.update(results)
            cache_data["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {"success": True, "message": "刷新成功"}
    except Exception as e:
        return {"success": False, "message": str(e)}


@app.route('/api/refresh/overview', methods=['POST'])
def refresh_overview():
    """单独刷新概览卡片 + 今日/明日未签收"""
    result = _login_and_refresh({
        "auto_monitor": get_auto_monitor_data,
        "today_unsigned": get_today_unsigned_data,
        "tomorrow_unsigned": get_tomorrow_unsigned_data,
    })
    return jsonify(result)


@app.route('/api/refresh/pending-detail', methods=['POST'])
def refresh_pending_detail():
    """单独刷新待配载订单 + 分省统计"""
    result = _login_and_refresh({
        "manual_query": get_manual_query_data,
        "auto_monitor": get_auto_monitor_data,
        "region_stats": get_region_stats_data,
    })
    return jsonify(result)


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
    """手动刷新待配载订单数据"""
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
                config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'province_config.txt')
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
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'province_config.txt')
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
                script_dir = os.path.dirname(os.path.abspath(__file__))
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
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'network_config.txt')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content:
                    SELECTED_NETWORKS = content.split(',')
    except:
        pass


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
                subprocess.Popen(['start', 'chrome', url], shell=True, stdout=subprocess.DEVNULL,
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
    print("   订单可视化看板 - 已启动")
    print("   正在打开浏览器...")
    print("   按 Ctrl+C 停止服务")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
