"""
零担订单分析页面 - 新增功能
基于零担面板.py扩展，增加订单分析、导出、图表功能
"""

import sys, os, re
import requests
import base64
import json
import time
import threading
from datetime import datetime, timedelta, timezone
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
from flask import Flask, jsonify, request
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed

# ====================== 核心配置 ======================
MY_ACCOUNT = "V0013992"
MY_PASSWORD = "Xs123456"
PUBLIC_KEY = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCctQTweXAiaQ3ct5bhj6nyisOQiGmgC/hUdK+QO9I9DudcQSUMxIXvMtpiogB9RWkAUC4b86x7SiGD6aCp7PbTspd5fLf8F6LUIj/BtmktQq7JNsShjAWBxCkE49HIIvPvl9rt8lO7MkgS2vUT04tEYeu/62ltOc3BljJXoPC4pQIDAQAB"
BASE_URL = "https://sdm.etransfar.com/jbl/api"
LOGIN_URL = f"{BASE_URL}/login/?_allow_anonymous=true"
QUERY_URL = f"{BASE_URL}/module-data/receive_management/page"
ASYNC_EXPORT_URL = f"{BASE_URL}/module-data/receive_management/async-export"
QUEUED_TASK_URL = BASE_URL + "/queued-task/my/{}"
AUTH_CODE_URL = BASE_URL + "/file/get-temporary-auth-code?key={}"
DOWNLOAD_URL = BASE_URL + "/file/download/{}?authCode={}"

# 网点配置
ALL_NETWORKS = {
    "江南": "713226235836239872",
    "非凡": "823427370722664448",
    "讯服": "823427183694450688",
    "江北": "713226114964791296",
    "零担": "740441957821714432"
}

# 导出字段
EXPORT_SORT_FIELDS = [
    "source_order_no", "exe_pur_order_b.order_date", "delivery_date", "receive_time",
    "stowage_all_weight", "signed_weight", "status_dk", "receive_name", "detailed_address",
    "receiver_name", "receiver_phone", "salesman_name", "receive_region_code", "material_name",
    "packaging_num", "plate_no", "carrier_name", "carrier_phone", "no", "order_no",
    "type_code", "sub_type_code", "expected_arrival_date", "k_contract_line_a.network",
    "exe_pur_order_b.pur_org_code", "created_date", "start_time", "receiver",
    "exe_pur_order_b.customer", "exe_pur_order_b.total_weight",
    "shipping_weight", "send_address", "send_name",
    "receipt_time", "exe_pur_order_b.customer_no",
    "logistics_time", "customer_prescription"
]

# ====================== 工具函数 ======================
def rsa_encrypt(password):
    pub_key = f"-----BEGIN PUBLIC KEY-----\n{PUBLIC_KEY}\n-----END PUBLIC KEY-----"
    rsa_key = RSA.import_key(pub_key)
    cipher = PKCS1_v1_5.new(rsa_key)
    encrypted = cipher.encrypt(password.encode())
    return base64.b64encode(encrypted).decode()

def login():
    """登录获取token"""
    session = requests.Session()
    payload = {
        "name": MY_ACCOUNT,
        "password": rsa_encrypt(MY_PASSWORD),
        "rememberMe": True,
        "imageCode": None,
        "loginBindingParameters": {}
    }
    headers = {"Content-Type": "application/json;charset=UTF-8", "User-Agent": "Mozilla/5.0"}
    resp = session.post(LOGIN_URL, json=payload, headers=headers, timeout=15)
    data = resp.json()
    if data.get("status") == "login":
        return session, data["token"]
    return None, None

def format_time(time_str):
    """UTC时间转北京时间"""
    if not time_str:
        return None
    try:
        dt_utc = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        dt_bj = dt_utc.astimezone(timezone(timedelta(hours=8)))
        return dt_bj.strftime("%Y-%m-%d %H:%M")
    except:
        return time_str

def extract_province(region_str):
    """从区域字符串提取省份"""
    if not region_str:
        return "未知"
    if "中国-" in region_str:
        parts = region_str.split("-")
        if len(parts) >= 2:
            return parts[1]
    for p in ["河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", 
              "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", 
              "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏", "新疆", "北京", "上海", "天津", "重庆"]:
        if p in region_str:
            return p
    return region_str[:6] if len(region_str) > 6 else region_str

def bj_date_to_utc(bj_date, hour=0, minute=0, second=0):
    """北京时间转UTC"""
    dt_bj = datetime(bj_date.year, bj_date.month, bj_date.day, hour, minute, second)
    return dt_bj.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

# ====================== 查询函数 ======================
def query_orders(session, token, rules, size=9999):
    """查询配载单数据"""
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
    resp = session.post(QUERY_URL, json=payload, headers=headers, timeout=60)
    return resp.json().get("content", [])

def parse_order(order):
    """解析订单数据"""
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
        "receive_region": order.get("receive_region_code_show"),
        "province": extract_province(order.get("receive_region_code_show")),
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
    }

# ====================== 统计分析函数 ======================
def analyze_by_province(orders):
    """按省份统计"""
    province_stats = {}
    for order in orders:
        province = order.get("province", "未知")
        if province not in province_stats:
            province_stats[province] = {"count": 0, "weight": 0}
        province_stats[province]["count"] += 1
        province_stats[province]["weight"] += order.get("stowage_weight", 0)
    
    return [{"province": k, "count": v["count"], "weight": round(v["weight"], 2)} 
            for k, v in sorted(province_stats.items(), key=lambda x: x[1]["weight"], reverse=True)]

def analyze_by_date(orders):
    """按日期统计"""
    date_stats = {}
    for order in orders:
        date_str = order.get("delivery_date", "")[:10] if order.get("delivery_date") else "未知"
        if date_str not in date_stats:
            date_stats[date_str] = {"count": 0, "weight": 0}
        date_stats[date_str]["count"] += 1
        date_stats[date_str]["weight"] += order.get("stowage_weight", 0)
    
    return [{"date": k, "count": v["count"], "weight": round(v["weight"], 2)} 
            for k, v in sorted(date_stats.items())]

def analyze_by_network(orders):
    """按网点统计"""
    network_stats = {}
    for order in orders:
        network = order.get("network", "未知")
        if network not in network_stats:
            network_stats[network] = {"count": 0, "weight": 0}
        network_stats[network]["count"] += 1
        network_stats[network]["weight"] += order.get("stowage_weight", 0)
    
    return [{"network": k, "count": v["count"], "weight": round(v["weight"], 2)} 
            for k, v in sorted(network_stats.items(), key=lambda x: x[1]["weight"], reverse=True)]

def analyze_by_status(orders):
    """按状态统计"""
    status_stats = {}
    for order in orders:
        status = order.get("status", "未知")
        if status not in status_stats:
            status_stats[status] = {"count": 0, "weight": 0}
        status_stats[status]["count"] += 1
        status_stats[status]["weight"] += order.get("stowage_weight", 0)
    
    return [{"status": k, "count": v["count"], "weight": round(v["weight"], 2)} 
            for k, v in sorted(status_stats.items(), key=lambda x: x[1]["count"], reverse=True)]

def analyze_by_logistics(orders):
    """按物流时效统计"""
    logistics_stats = {}
    for order in orders:
        logistics = order.get("logistics_time") or "未签收"
        if logistics not in logistics_stats:
            logistics_stats[logistics] = {"count": 0, "weight": 0}
        logistics_stats[logistics]["count"] += 1
        logistics_stats[logistics]["weight"] += order.get("stowage_weight", 0)
    
    return [{"logistics": k, "count": v["count"], "weight": round(v["weight"], 2)} 
            for k, v in sorted(logistics_stats.items(), key=lambda x: x[1]["count"], reverse=True)]

def analyze_daily_province(orders):
    """按日期+省份统计（用于堆积柱状图）"""
    daily_province = {}
    for order in orders:
        date_str = order.get("delivery_date", "")[:10] if order.get("delivery_date") else "未知"
        province = order.get("province", "未知")
        
        if date_str not in daily_province:
            daily_province[date_str] = {}
        if province not in daily_province[date_str]:
            daily_province[date_str][province] = 0
        daily_province[date_str][province] += order.get("stowage_weight", 0)
    
    # 获取所有省份
    all_provinces = set()
    for date_data in daily_province.values():
        all_provinces.update(date_data.keys())
    
    # 构建图表数据
    dates = sorted(daily_province.keys())
    series = []
    for province in sorted(all_provinces):
        data = [round(daily_province.get(d, {}).get(province, 0), 2) for d in dates]
        series.append({"name": province, "data": data})
    
    return {"dates": dates, "series": series}

# ====================== 导出函数 ======================
def get_auth_headers(token):
    """获取认证头"""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "language": "zh_CN",
        "timezone": "UTC+0800"
    }

def submit_export(session, token, rules):
    """提交异步导出任务"""
    headers = get_auth_headers(token)
    payload = {
        "direction": "DESC",
        "property": "id",
        "fromClientType": "pc",
        "number": 0,
        "sorts": [],
        "rules": rules,
        "size": 9999,
        "specialConditions": [],
        "dynamicFormCode": "stowage_sign_receipt",
        "developmentSystemId": None,
        "debugFlag": False,
        "exportSortFields": EXPORT_SORT_FIELDS
    }
    resp = session.post(ASYNC_EXPORT_URL, json=payload, headers=headers, timeout=30)
    return resp.json()

def poll_task(session, token, task_id, max_wait=120):
    """轮询任务状态"""
    headers = get_auth_headers(token)
    start_time = time.time()
    
    while time.time() - start_time < max_wait:
        resp = session.get(QUEUED_TASK_URL.format(task_id), headers=headers, timeout=15)
        if resp.status_code == 200:
            task_data = resp.json()
            status = task_data.get("statusEk", "")
            
            if status == "SUCCEED":
                output_param = task_data.get("outputParam", {})
                content_str = output_param.get("content", "")
                if content_str:
                    content = json.loads(content_str)
                    attachment = content.get("attachment", {})
                    attach_files = attachment.get("attachFile", [])
                    if attach_files:
                        return attach_files[0].get("key")
            elif status in ("FAILED", "ERROR"):
                return None
        
        time.sleep(3)
    
    return None

def download_file(session, token, file_key, save_path):
    """下载文件"""
    # 获取授权码
    headers = get_auth_headers(token)
    auth_resp = session.get(AUTH_CODE_URL.format(file_key), headers=headers, timeout=15)
    auth_code = auth_resp.json().get("temporaryAuthCode")
    
    if not auth_code:
        return False
    
    # 下载文件
    download_resp = session.get(DOWNLOAD_URL.format(file_key, auth_code), headers=headers, timeout=60)
    if download_resp.status_code == 200:
        with open(save_path, "wb") as f:
            f.write(download_resp.content)
        return True
    
    return False

# ====================== Flask路由 ======================
app = Flask(__name__)
CORS(app)

@app.route('/api/order-analysis/query', methods=['POST'])
def api_query_orders():
    """查询订单"""
    try:
        data = request.get_json()
        
        # 构建查询条件
        rules = []
        
        # 网点筛选
        networks = data.get('networks', [])
        if networks:
            network_ids = [ALL_NETWORKS[n] for n in networks if n in ALL_NETWORKS]
            if network_ids:
                rules.append({"field": "k_contract_line_a.network", "option": "EQ", "values": network_ids[0]})
        
        # 创建时间范围
        created_start = data.get('created_start')
        created_end = data.get('created_end')
        if created_start and created_end:
            rules.append({"field": "created_date", "option": "BTS", "values": [created_start, created_end]})
        
        # 要求到货时间范围
        delivery_start = data.get('delivery_start')
        delivery_end = data.get('delivery_end')
        if delivery_start and delivery_end:
            rules.append({"field": "delivery_date", "option": "BTS", "values": [delivery_start, delivery_end]})
        
        # 物流时效
        logistics = data.get('logistics_time')
        if logistics and logistics != '全部':
            rules.append({"field": "logistics_time", "option": "EQ", "values": [logistics]})
        
        # 配载单状态
        status = data.get('status')
        if status and status != '全部':
            status_map = {
                "待配载": "TO_BE_STOWED", "已配载": "STOWED", 
                "已发车确认": "DEPARTED_CONFIRMED", "已签收": "SIGNED_IN",
                "已回单确认": "RECEIPT_CONFIRMED"
            }
            if status in status_map:
                rules.append({"field": "status_dk", "option": "EQ", "values": [status_map[status]]})
        
        # 登录并查询
        session, token = login()
        if not token:
            return jsonify({"success": False, "message": "登录失败"})
        
        orders_raw = query_orders(session, token, rules)
        orders = [parse_order(o) for o in orders_raw]
        
        return jsonify({
            "success": True,
            "data": orders,
            "total": len(orders),
            "total_weight": round(sum(o["stowage_weight"] for o in orders), 2)
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/order-analysis/analyze', methods=['POST'])
def api_analyze_orders():
    """分析订单数据"""
    try:
        data = request.get_json()
        orders = data.get('orders', [])
        
        # 执行多维度分析
        result = {
            "by_province": analyze_by_province(orders),
            "by_date": analyze_by_date(orders),
            "by_network": analyze_by_network(orders),
            "by_status": analyze_status(orders) if 'analyze_status' in dir() else analyze_by_status(orders),
            "by_logistics": analyze_by_logistics(orders),
            "daily_province": analyze_daily_province(orders)
        }
        
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/order-analysis/export', methods=['POST'])
def api_export_orders():
    """导出订单"""
    try:
        data = request.get_json()
        rules = data.get('rules', [])
        
        session, token = login()
        if not token:
            return jsonify({"success": False, "message": "登录失败"})
        
        # 提交导出任务
        result = submit_export(session, token, rules)
        if result.get("status") != "success":
            return jsonify({"success": False, "message": result.get("message", "导出失败")})
        
        task_id = result.get("data", {}).get("id")
        
        # 轮询等待完成
        file_key = poll_task(session, token, task_id)
        if not file_key:
            return jsonify({"success": False, "message": "导出超时或失败"})
        
        # 下载文件
        save_dir = os.path.dirname(os.path.abspath(__file__))
        filename = f"配载单导出_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        save_path = os.path.join(save_dir, filename)
        
        if download_file(session, token, file_key, save_path):
            return jsonify({"success": True, "filename": filename, "path": save_path})
        else:
            return jsonify({"success": False, "message": "下载失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/api/order-analysis/networks')
def api_get_networks():
    """获取网点列表"""
    return jsonify({"success": True, "data": list(ALL_NETWORKS.keys())})

# ====================== 启动 ======================
if __name__ == '__main__':
    print("=" * 50)
    print("  零担订单分析系统")
    print("  访问地址: http://localhost:5001")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5001, debug=True)
