# 零担面板 API 接口文档

> 本文档描述了 `零担面板.py` 中所有的 API 接口、字段定义、认证方式及调用方法。
> 供其他 AI 系统调用和数据爬取使用。

---

## 一、认证信息

### 1.1 账号配置
```python
MY_ACCOUNT = "V0013992"
MY_PASSWORD = "Xs123456"
```

### 1.2 RSA 公钥（用于密码加密）
```
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCctQTweXAiaQ3ct5bhj6nyisOQiGmgC/hUdK+QO9I9DudcQSUMxIXvMtpiogB9RWkAUC4b86x7SiGD6aCp7PbTspd5fLf8F6LUIj/BtmktQq7JNsShjAWBxCkE49HIIvPvl9rt8lO7MkgS2vUT04tEYeu/62ltOc3BljJXoPC4pQIDAQAB
```

### 1.3 Token 获取方式
- **登录接口**: `POST https://sdm.etransfar.com/jbl/api/login/?_allow_anonymous=true`
- **请求头**: `Content-Type: application/json;charset=UTF-8`
- **请求体**:
  ```json
  {
    "name": "V0013992",
    "password": "<RSA加密后的密码>",
    "rememberMe": true,
    "imageCode": null,
    "loginBindingParameters": {}
  }
  ```
- **响应**: 成功时返回 `{"status": "login", "token": "..."}`
- **使用方式**: 后续请求在 Header 中添加 `Authorization: Bearer <token>`

---

## 二、外部系统 API（数据源）

### 2.1 采购订单查询

- **URL**: `POST https://sdm.etransfar.com/jbl/api/module-data/purchase_order/page`
- **动态表单代码**: `purchase_order`
- **用途**: 查询待配载订单

#### 请求体模板
```json
{
  "direction": "DESC",
  "property": "id",
  "fromClientType": "pc",
  "ignoreField": false,
  "number": 0,
  "dynamicFormCode": "purchase_order",
  "rules": [
    {"field": "receive_region_code_show", "option": "LIKE_ANYWHERE", "values": ["<省份>"]},
    {"field": "status_dk_show", "option": "EQ", "values": ["待配载"]}
  ],
  "size": 9999999,
  "sorts": [
    {"property": "required_arrival_date", "direction": "ASC"},
    {"property": "receive_region_code", "direction": "ASC"}
  ],
  "specialConditions": []
}
```

#### 可用筛选字段
| 字段名 | 说明 | 筛选方式 |
|--------|------|----------|
| `receive_region_code_show` | 收货地区 | LIKE_ANYWHERE（模糊匹配） |
| `send_region_code_show` | 发货地区 | LIKE_ANYWHERE（模糊匹配） |
| `status_dk_show` | 订单状态 | EQ（精确匹配，值如"待配载"） |

#### 返回字段
| 字段名 | 说明 | 示例 |
|--------|------|------|
| `source_order_no` | 订单号 | "PO20260527001" |
| `receive_region_code_show` | 收货地区 | "湖南省" |
| `send_region_code_show` | 发货地区 | "广东省" |
| `total_weight` | 总重量 | 1500.5 |
| `stowage_all_weight` | 配载重量 | 1200.0 |
| `all_send_storage_code_show` | 发货仓库 | "主仓" |
| `order_date` | 下单时间（UTC） | "2026-05-27T08:00:00.000Z" |
| `urgent_flag_custom` | 加急标记 | "是/否" |
| `the_way_flag_custom` | 在途标记 | "是/否" |

---

### 2.2 收货管理查询（签收查询）

- **URL**: `POST https://sdm.etransfar.com/jbl/api/module-data/receive_management/page`
- **动态表单代码**: `stowage_sign_receipt`
- **用途**: 查询签收状态、今明未签收订单、近7天重量趋势

#### 请求体模板（今/明日未签收）
```json
{
  "debugFlag": false,
  "developmentSystemId": null,
  "direction": "DESC",
  "dynamicFormCode": "stowage_sign_receipt",
  "fromClientType": "pc",
  "number": 0,
  "property": "id",
  "rules": [
    {"field": "delivery_date", "option": "BTS", "values": ["<开始UTC时间>", "<结束UTC时间>"]},
    {"field": "k_contract_line_a.network", "option": "IN", "values": ["<网点ID>"]}
  ],
  "size": 100,
  "sorts": [{"property": "receive_time", "direction": "DESC"}],
  "specialConditions": []
}
```

#### 请求体模板（近7天趋势）
```json
{
  "debugFlag": false,
  "developmentSystemId": null,
  "direction": "DESC",
  "dynamicFormCode": "stowage_sign_receipt",
  "fromClientType": "pc",
  "number": 0,
  "property": "id",
  "rules": [
    {"field": "exe_pur_order_b.order_date", "option": "BTS", "values": ["<开始UTC时间>", "<结束UTC时间>"]},
    {"field": "k_contract_line_a.network", "option": "IN", "values": ["<网点ID>"]}
  ],
  "size": 999,
  "sorts": [],
  "specialConditions": []
}
```

#### 可用筛选字段
| 字段名 | 说明 | 筛选方式 |
|--------|------|----------|
| `delivery_date` | 需求到货时间 | BTS（时间段） |
| `exe_pur_order_b.order_date` | 下单时间 | BTS（时间段） |
| `k_contract_line_a.network` | 网点 | IN（包含） |

#### 返回字段
| 字段名 | 说明 | 示例 |
|--------|------|------|
| `source_order_no` | 订单号 | "PO20260527001" |
| `receive_name` | 收货方名称 | "长沙某公司" |
| `receiver_phone` | 收货方电话 | "13800138000" |
| `province_id_show` | 省份 | "湖南" |
| `city_id_show` | 城市 | "长沙" |
| `district_id_show` | 区县 | "岳麓区" |
| `street_id_show` | 街道 | "麓谷街道" |
| `detailed_address` | 详细地址 | "麓谷大道100号" |
| `receive_address` | 完整收货地址 | "湖南省长沙市岳麓区麓谷大道100号" |
| `status_dk_show` | 签收状态 | "已签收/未签收" |
| `receive_time` | 签收时间（UTC） | "2026-05-27T10:00:00.000Z" |
| `delivery_date` | 需求到货时间（UTC） | "2026-05-27T00:00:00.000Z" |
| `stowage_all_weight` | 配载总重量 | 1200.0 |

#### 响应中的汇总字段
```json
{
  "totalSumData": {
    "stowage_all_weight": 15000.5
  }
}
```

---

### 2.3 供应商异常查询（KPI处罚）

- **URL**: `POST https://sdm.etransfar.com/jbl/api/module-data/supplier_abnormal_tabul/page`
- **动态表单代码**: `supplier_abnormal_tabul`
- **用途**: 查询KPI类处罚记录

#### 请求体模板
```json
{
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
```

#### 可用筛选字段
| 字段名 | 说明 | 筛选方式 |
|--------|------|----------|
| `abnormal_category_dk` | 异常类别 | EQ（精确匹配，值为"KPICLASS"表示KPI类） |

#### 返回字段
| 字段名 | 说明 | 示例 |
|--------|------|------|
| `exception_no` | 异常编号 | "AB202605270044" |
| `oder_number_show` | 关联订单号 | "PO20260527001" |
| `carrier_show` | 承运商 | "XX物流" |
| `driver` | 司机 | "张三" |
| `license_plate` | 车牌号 | "湘A12345" |
| `abnormal_subclass_dk_show` | 异常子类 | "延迟送达" |
| `problem_descripetion` | 问题描述 | "延迟送达2小时，扣绩效考核5分" |
| `dynamic_form_value_id` | 记录ID（用于查详情） | "375549423855472640" |

---

### 2.4 KPI详情查询

- **URL**: `GET https://sdm.etransfar.com/jbl/api/module-data/supplier_abnormal/supplier_abnormal/375549423855472640/{dynamic_form_value_id}`
- **用途**: 获取单条KPI处罚的详细信息（合同、考核扣分等）

#### 返回字段
| 字段名 | 说明 | 示例 |
|--------|------|------|
| `data.exception_handling_a.related_contract_no_show` | 关联合同号 | "HT2026001" |
| `data.exception_handling_a.appraisal_results` | 考核扣分数 | 5.0 |
| `data.exception_handling_a.customer` | 客户名称 | "长沙某公司" |
| `data.exception_handling_a.send_region_code_show` | 发货地区 | "广东省" |
| `data.exception_handling_a.receive_region_code_show` | 收货地区 | "湖南省" |

---

## 三、内部 Flask API（面板接口）

> 面板默认运行在 `http://localhost:5000`

### 3.1 仪表板数据

#### `GET /api/dashboard`
获取所有缓存的仪表板数据。

**响应字段**:
```json
{
  "success": true,
  "data": {
    "manual_query": {},
    "auto_monitor": {},
    "region_stats": {},
    "today_unsigned": {},
    "tomorrow_unsigned": {},
    "sender_region_orders": {},
    "kpi_penalty": {},
    "last_update": "2026-05-27 10:00:00"
  },
  "selected_provinces": ["湖南", "湖北", "新疆", "河北", "安徽"],
  "all_provinces": ["河北", "山西", "辽宁", "..."],
  "server_time": "2026-05-27 10:00:00"
}
```

---

### 3.2 健康检查

#### `GET /api/health`
**响应**:
```json
{
  "status": "ok",
  "last_update": "2026-05-27 10:00:00"
}
```

---

### 3.3 今/明日未签收订单

#### `GET /api/today-unsigned?province=<省份>`
#### `GET /api/tomorrow-unsigned?province=<省份>`

**查询参数**:
- `province`: 可选，省份名称筛选，如"湖南"，不传或"全部"返回所有

**响应字段**:
```json
{
  "total_orders": 100,
  "unsigned_count": 25,
  "unsigned_orders": [
    {
      "order_no": "PO20260527001",
      "receive_name": "长沙某公司",
      "receiver_phone": "13800138000",
      "province": "湖南",
      "city": "长沙",
      "district": "岳麓区",
      "street": "麓谷街道",
      "detailed_address": "麓谷大道100号",
      "address": "长沙岳麓区",
      "status": "未签收",
      "receive_time": "2026-05-27 10:00:00",
      "delivery_date": "2026-05-27 08:00:00",
      "signed_weight": 1200.0
    }
  ],
  "provinces": ["湖南", "湖北", "广东"]
}
```

---

### 3.4 近7天重量趋势

#### `GET /api/weekly-weight`
**响应字段**:
```json
{
  "labels": ["05-20", "05-21", "05-22", "05-23", "05-24", "05-25", "05-26"],
  "data": [15000.5, 18000.2, 12000.0, 20000.8, 16000.3, 19000.1, 17000.5]
}
```

---

### 3.5 KPI处罚数据

#### `GET /api/kpi-penalty`
**响应字段**:
```json
{
  "current_period": {
    "orders": [
      {
        "exception_no": "AB202605270044",
        "order_no": "PO20260527001",
        "carrier": "XX物流",
        "driver": "张三",
        "license_plate": "湘A12345",
        "subclass": "延迟送达",
        "description": "延迟送达2小时",
        "score": 5.0,
        "date": "2026-05-27",
        "contract": "HT2026001",
        "customer": "长沙某公司",
        "send_region": "广东省",
        "receive_region": "湖南省"
      }
    ],
    "total_score": 15.5,
    "count": 3
  },
  "previous_period": {},
  "periods": {
    "current": {"start": "2026-04-23", "end": "2026-05-22"},
    "previous": {"start": "2026-03-23", "end": "2026-04-22"}
  }
}
```

---

### 3.6 待配载订单列表

#### `GET /api/manual-orders?province=<省份>`
**查询参数**:
- `province`: 可选，省份名称筛选

**响应字段**:
```json
{
  "orders": [
    {
      "order_no": "PO20260527001",
      "region": "湖南省",
      "weight": 1500.5,
      "warehouse": "主仓",
      "order_date": "2026-05-27 16:00:00",
      "urgent_flag_custom": "否",
      "the_way_flag_custom": "是"
    }
  ],
  "total_count": 50,
  "total_weight": 75000.0,
  "provinces": ["湖南", "湖北", "新疆"]
}
```

---

### 3.7 发货省份待配载订单

#### `GET /api/sender-region-orders?province=<省份>`
**查询参数**:
- `province`: 可选，发货省份名称筛选

**响应字段**:
```json
{
  "orders": [
    {
      "order_no": "PO20260527001",
      "sender_region": "广东省",
      "receive_region": "湖南省",
      "weight": 1500.5,
      "stowage_weight": 1200.0,
      "warehouse": "主仓",
      "create_time": "2026-05-27 16:00:00",
      "on_the_way": "是"
    }
  ],
  "total_count": 50,
  "total_weight": 75000.0,
  "region_details": [
    {"region": "湖南", "count": 20, "weight": 30000.0},
    {"region": "湖北", "count": 15, "weight": 22500.0}
  ],
  "provinces": ["湖南", "湖北"]
}
```

---

### 3.8 近7天订单详情

#### `POST /api/weekly-orders`
**响应字段**:
```json
{
  "success": true,
  "data": [
    {
      "date": "2026-05-20",
      "count": 100,
      "total_weight": 150000.5,
      "orders": [
        {
          "order_no": "PO20260520001",
          "stowage_weight": 1500.0,
          "receive_name": "长沙某公司",
          "province": "湖南",
          "create_time": "2026-05-20 16:00:00"
        }
      ]
    }
  ]
}
```

---

### 3.9 省份配置

#### `GET /api/provinces`
获取当前选中和全部省份列表。

#### `POST /api/provinces`
设置选中的省份列表。
```json
{
  "provinces": ["湖南", "湖北", "广东"]
}
```

---

### 3.10 网点配置

#### `GET /api/networks`
获取当前选中和全部网点列表。

#### `POST /api/networks`
设置选中的网点列表。
```json
{
  "networks": ["零担", "江南"]
}
```

**网点ID对照表**:
| 网点名称 | 网点ID |
|----------|--------|
| 江南 | 713226235836239872 |
| 非凡 | 823427370722664448 |
| 讯服 | 823427183694450688 |
| 江北 | 713226114964791296 |
| 零担 | 740441957821714432 |

---

### 3.11 数据刷新接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/refresh` | POST | 全局刷新，可传入 `{"provinces": [...], "networks": [...]}` |
| `/api/refresh/overview` | POST | 刷新概览卡片 + 今日/明日未签收 |
| `/api/refresh/pending-detail` | POST | 刷新待配载订单 + 分省统计 |
| `/api/refresh/sender-region` | POST | 刷新发货省份待配载订单 |
| `/api/refresh/weekly` | POST | 刷新近7天趋势图 |
| `/api/refresh/kpi` | POST | 刷新KPI处罚数据 |
| `/api/refresh-manual` | POST | 手动刷新待配载订单数据 |
| `/api/refresh-pending` | POST | 仅刷新待配载订单相关数据 |

---

## 四、调用示例（Python）

### 4.1 登录获取 Token
```python
import requests
import base64
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5

def rsa_encrypt(password, public_key):
    pub_key = f"-----BEGIN PUBLIC KEY-----\n{public_key}\n-----END PUBLIC KEY-----"
    rsa_key = RSA.importKey(pub_key)
    cipher = PKCS1_v1_5.new(rsa_key)
    encrypted = cipher.encrypt(password.encode())
    return base64.b64encode(encrypted).decode()

PUBLIC_KEY = "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCctQTweXAiaQ3ct5bhj6nyisOQiGmgC/hUdK+QO9I9DudcQSUMxIXvMtpiogB9RWkAUC4b86x7SiGD6aCp7PbTspd5fLf8F6LUIj/BtmktQq7JNsShjAWBxCkE49HIIvPvl9rt8lO7MkgS2vUT04tEYeu/62ltOc3BljJXoPC4pQIDAQAB"

payload = {
    "name": "V0013992",
    "password": rsa_encrypt("Xs123456", PUBLIC_KEY),
    "rememberMe": True,
    "imageCode": None,
    "loginBindingParameters": {}
}

resp = requests.post(
    "https://sdm.etransfar.com/jbl/api/login/?_allow_anonymous=true",
    json=payload,
    headers={"Content-Type": "application/json;charset=UTF-8"}
)
token = resp.json().get("token")
print(f"Token: {token}")
```

### 4.2 查询待配载订单
```python
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
        {"field": "receive_region_code_show", "option": "LIKE_ANYWHERE", "values": ["湖南"]},
        {"field": "status_dk_show", "option": "EQ", "values": ["待配载"]}
    ],
    "size": 9999999,
    "sorts": [
        {"property": "required_arrival_date", "direction": "ASC"}
    ],
    "specialConditions": []
}

resp = requests.post(
    "https://sdm.etransfar.com/jbl/api/module-data/purchase_order/page",
    json=payload,
    headers=headers
)
orders = resp.json().get("content", [])
```

### 4.3 调用面板内部 API
```python
# 获取仪表板数据
resp = requests.get("http://localhost:5000/api/dashboard")
data = resp.json()

# 获取今日未签收订单（筛选湖南）
resp = requests.get("http://localhost:5000/api/today-unsigned?province=湖南")
unsigned = resp.json()

# 触发数据刷新
resp = requests.post("http://localhost:5000/api/refresh", json={
    "provinces": ["湖南", "湖北"],
    "networks": ["零担"]
})
```

---

## 五、时间格式说明

- **外部 API 时间格式**: UTC 时间，如 `2026-05-27T08:00:00.000Z`
- **面板返回时间格式**: 北京时间（UTC+8），如 `2026-05-27 16:00:00`
- **时间转换**: 外部 API 返回的时间需 +8 小时转为北京时间

---

## 六、KPI 考核周期

- **周期规则**: 每月23日到次月22日为一个考核周期
- **示例**: 
  - 当前周期: 2026-04-23 ~ 2026-05-22
  - 上一周期: 2026-03-23 ~ 2026-04-22

---

## 七、配置文件

| 文件名 | 说明 | 格式 |
|--------|------|------|
| `province_config.txt` | 省份配置 | 逗号分隔，如 `湖南,湖北,新疆` |
| `network_config.txt` | 网点配置 | 逗号分隔，如 `零担,江南` |

---

## 八、错误处理

- 登录失败: Token 为 `None`，需检查账号密码
- 查询超时: 默认超时 15-20 秒
- 未签收判断: `status_dk_show != "已签收"`
- KPI扣分提取: 从 `problem_descripetion` 字段正则匹配，格式如"扣绩效考核5分"
