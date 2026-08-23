"""数据解析服务 - CSV/XLSX 读取、编码识别、列名映射

复用现有分析逻辑,从'退费汇总.xlsx'的列结构提取列模板
"""

import re
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import settings

# 各数据类型的列模板(目标字段名 -> 样本源列名候选)
# 源列名可能含尾部 \t,解析时会清洗
COLUMN_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "refund": {
        "refund_no": ["退费单号"],
        "platform_order_no": ["平台订单号"],
        "third_order_no": ["三方订单号"],
        "device_sn": ["设备SN"],
        "refund_reason": ["退费原因"],
        "package_name": ["套餐名称"],
        "merchant_name": ["商户名称"],
        "refund_amount": ["退费金额"],
        "actual_amount": ["实退金额"],
        "remark": ["备注"],
        "status": ["状态"],
        "refund_method": ["退款方式"],
        "audit_remark": ["审核备注"],
        "auditor": ["审核人"],
        "operator": ["操作人"],
        "created_at_raw": ["创建时间"],
        "updated_at_raw": ["更新时间"],
        "package_price": ["套餐价格"],
    },
    "wallet": {
        "account_id": ["账户ID"],
        "wallet_balance": ["钱包余额"],
        "device_sn": ["SN"],
        "op_type": ["操作类型"],
        "op_amount": ["操作金额"],
        "remark": ["备注"],
        "op_time": ["操作时间"],
        "operator": ["操作人员"],
    },
    "device_combo": {
        "recharge_order_no": ["充值订单号"],
        "device_sn": ["设备SN"],
        "package_name": ["套餐名称"],
        "package_type": ["套餐类型"],
        "traffic_type": ["流量类型"],
        "merchant_name": ["商户名称"],
        "actual_amount": ["实际支付金额"],
        "start_date": ["开始日期"],
        "end_date": ["结束日期"],
        "updated_at_raw": ["更新时间"],
        "created_at_raw": ["创建时间"],
        "valid_days": ["套餐有效天数"],
        "creator": ["创建人"],
        "available_traffic": ["可用流量"],
        "used_traffic": ["已用流量"],
        "remaining_traffic": ["剩余流量"],
        "status": ["当前状态"],
    },
    "ticket": {
        "ticket_no": ["工单号"],
        "device_sn": ["SN"],
        "device_type": ["设备类型"],
        "device_type_remark": ["设备类型备注"],
        "phone": ["手机号"],
        "service_category": ["服务类别", "服务类型"],
        "service_item": ["服务项"],
        "priority": ["优先级"],
        "status": ["状态"],
        "remark": ["备注"],
        "register_time": ["登记时间"],
        "active_time": ["激活时间"],
        "dispatch_time": ["分发时间"],
        "finish_time": ["完成时间"],
        "handle_duration": ["处理时长"],
        "registrant": ["登记人"],
        "channel": ["录入渠道", "登记渠道"],
        "handle_node": ["处理节点"],
        "handler": ["处理人"],
        "handle_opinion": ["处理意见"],
        "is_appeal": ["是否申诉"],
        "order_no": ["订单编号"],
        "refund_amount": ["退款金额"],
        "estimated_amount": ["预估可退金额"],
        # ── 催单 & 派单 新增 8 列 (2026-08-03) ──
        "urge_count": ["催单次数"],
        "urge_status": ["催单状态", "催"],
        "last_dispatch_time": ["最后派单时间"],
        "assignee": ["分配人员"],
        "contact_person": ["联系人员"],
        "dispatcher": ["派发人员"],
        "last_urge_by": ["最后催单人"],
        "last_urge_time": ["最后催单时间"],
    },
    "merchant_refund": {
        "trade_time": ["交易时间"],
        "app_id": ["公众账号ID"],
        "merchant_id": ["商户号"],
        "sub_merchant_id": ["特约商户号"],
        "device_no": ["设备号"],
        "wx_order_no": ["微信订单号"],
        "merchant_order_no": ["商户订单号"],
        "user_id": ["用户标识"],
        "trade_type": ["交易类型"],
        "trade_status": ["交易状态"],
        "pay_bank": ["付款银行"],
        "currency": ["货币种类"],
        "settle_amount": ["应结订单金额"],
        "voucher_amount": ["代金券金额"],
        "wx_refund_no": ["微信退款单号"],
        "merchant_refund_no": ["商户退款单号"],
        "refund_amount": ["退款金额"],
        "recharge_voucher_refund": ["充值券退款金额"],
        "refund_type": ["退款类型"],
        "refund_status": ["退款状态"],
        "product_name": ["商品名称"],
        "fee": ["手续费"],
        "rate": ["费率"],
        "order_amount": ["订单金额"],
        "apply_refund_amount": ["申请退款金额"],
    },
    # 闪猫退款(已退款申请) —— 根据 2026-08-03 实际 Excel 样例精确定义列名
    # 实际列(14列):退费单号,订单号,手机号码,套餐名称,实际支付金额,申请退款金额,退款金额,
    #             退款原因,退款状态,退款方式,审核人名称,审核时间,审核备注,申请备注
    "shanmao_refund": {
        "refund_no": ["退费单号", "退款单号", "退款编号", "申请单号", "流水号", "退款申请号"],
        "order_no": ["订单号", "关联订单号", "订单编号", "平台订单号"],
        "phone": ["手机号码", "手机号", "联系电话", "用户手机"],
        "device_sn": ["设备SN", "SN码", "SN", "设备编号", "设备号"],
        "paid_amount": ["实际支付金额", "支付金额", "订单支付金额", "实付金额"],
        "refund_amount": ["申请退款金额", "退款金额", "申请金额"],
        "actual_amount": ["退款金额", "实退金额", "实际退款金额", "到账金额"],
        "package_name": ["套餐名称", "商品名称", "产品名称", "套餐"],
        "merchant_name": ["商户名称", "商户", "店铺名称", "所属商户"],
        "refund_reason": ["退款原因", "退款说明", "申请原因", "原因"],
        "refund_method": ["退款方式", "退款渠道", "到账方式", "支付方式"],
        "status": ["退款状态", "状态", "申请状态", "审核状态"],
        "applicant": ["申请人", "提交人", "创建人", "登记人"],
        "auditor": ["审核人名称", "审核人", "审批人"],
        "operator": ["操作人", "处理人", "打款人", "退款操作人"],
        "apply_time": ["申请时间", "提交时间", "创建时间", "登记时间"],
        "audit_time": ["审核时间", "审批时间"],
        "refund_time": ["退款时间", "完成时间", "到账时间", "实际退款时间"],
        "remark": ["申请备注", "备注", "说明"],
        "audit_remark": ["审核备注", "审批意见", "审核说明"],
    },
    # 手动退款登记(商户号手机退款处理登记) —— 客服在商户号后台手动操作退款台账
    # 实际列(12列):登记日期,设备号,订单号,商户ID,订单金额,登记人,是否已退款,
    #             退款金额,退款操作人,操作时间,图片(仅展示,不入库),备注
    "manual_refund": {
        "register_time": ["登记日期", "登记时间"],
        "device_sn": ["设备号", "设备SN", "SN"],
        "order_no": ["订单号", "订单编号", "平台订单号"],
        "merchant_id": ["商户ID", "商户号", "商户号ID"],
        "order_amount": ["订单金额", "订单原金额"],
        "registrant": ["登记人"],
        "is_refunded": ["是否已退款", "是否退款"],
        "refund_amount": ["退款金额", "实退金额"],
        "refund_operator": ["退款操作人", "操作人", "处理人"],
        "op_time": ["操作时间", "退款时间", "完成时间"],
        "remark": ["备注", "说明"],
    },
    "merchant": {
        "sub_name": ["子商户名称"],
        "main_name": ["主商户名称"],
        "merchant_id": ["商户号id"],
        "legal_person": ["法人"],
        "super_admin": ["超级管理员"],
    },
    "cs_record": {
        "problem_category": ["问题大类"],
        "problem_type": ["问题类型"],
        "device_sn": ["设备号"],
        "solution": ["方案"],
        "operation_time": ["操作时间"],
        "operator": ["操作人"],
        "send_content": ["发送内容"],
        "description": ["说明"],
        "execution_result": ["执行结果说明"],
        "user_accepted": ["用户是否接受"],
        "consult_time": ["咨询时间"],
        "problem_create_time": ["问题类型创建时间"],
        "last_consult_time": ["最后咨询时间"],
    },
    "external_complaint": {
        "complaint_no": ["投诉编号"],
        "complaint_date": ["投诉日期"],
        "platform": ["平台"],
        "complaint_account": ["投诉账号"],
        "company_entity": ["公司主体"],
        "complaint_shop": ["投诉店铺"],
        "purchase_order_no": ["购买订单编号"],
        "complainant": ["投诉人"],
        "complainant_phone": ["投诉手机号"],
        "device_sn": ["投诉设备号"],
        "wechat_id": ["微信账户id"],
        "problem": ["投诉问题"],
        "keyword": ["投诉关键词"],
        "complaint_category": ["投诉分类"],
        "solution": ["解决方案"],
        "result": ["结果"],
        "follower": ["跟进人"],
        "handle_time": ["处理时间"],
        "handle_detail": ["处理明细"],
    },
    "card_complaint": {
        "complaint_date": ["投诉日期"],
        "complaint_group": ["投诉群"],
        "complainant": ["投诉人"],
        "complainant_phone": ["投诉手机号"],
        "device_sn": ["设备号"],
        "problem": ["投诉问题"],
        "problem_category": ["投诉问题.1"],
        "result": ["结果"],
        "solution": ["解决方案"],
        "handler": ["处理人"],
        "handle_result": ["处理结果"],
    },
    # 设备标签(标签字典 + 打标明细,同一次导出可能含两张 sheet 或一张合并表)
    # 常见 BMS 标签字典列名候选
    # 设备标签定义: BMS 后台实际不单独导出标签字典(通常只导出打标明细 + 部分元数据)。
    # 这里留作手动维护 / 从打标明细里抽取 tag_name 反推的导入模板支持,同时兼容未来可能导出的
    # "ID / 标签 / 类型 / 颜色 / 描述 / 创建时间" 字段顺序。
    "device_tag": {
        "id": ["标签ID", "id", "ID"],
        "tag_name": ["标签名称", "标签名", "标签", "名称", "tag_name"],
        "tag_type": ["标签类型", "类型", "分类", "tag_type"],
        "color": ["颜色", "标签颜色", "显示颜色"],
        "description": ["描述", "备注", "说明", "标签描述"],
        "created_at": ["创建时间", "生成时间", "添加时间"],
    },
    # 设备标签打标明细(BMS 「device/device-tags」页点导出实际输出的表结构,
    # 实测 2026-08-05 导出列: 设备sn,标签名,设备所属商户,设备当前套餐,创建人,更新时间)
    "device_tag_binding": {
        "device_sn": ["设备SN", "SN", "SN码", "设备编号", "设备号", "设备sn", "sn"],
        "tag_id": ["标签ID", "标签id", "tag_id"],
        "tag_name": ["标签名称", "标签名", "标签", "tag_name"],
        "created_by": ["打标人", "创建人", "操作人", "添加人", "创建员", "最后修改人", "修改人"],
        "created_at": ["打标时间", "创建时间", "添加时间", "绑定时间", "更新时间"],
        # BMS 顺带导出的设备维度信息,非打标明细主键字段:
        # 设备所属商户/设备当前套餐 — 作为导入时的 _sync_devices 辅助信息填进 devices 表
        # (data_parser apply_mapping 遇到非模型字段会忽略,因此列名不冲突;需注意保持列名可读性)
        "_merchant_name": ["设备所属商户", "所属商户", "商户"],
        "_package_name": ["设备当前套餐", "当前套餐", "套餐"],
    },
    # 人力快照 → SCD2 任职历史(走 /persons/import-snapshot,不走 UPSERT_DISPATCH)
    "staff": {
        "display_name": ["姓名"],
        "alias_kefu": ["客服名"],
        "alias_name1": ["名称1"],
        "alias_name2": ["名称2"],
        "remark": ["备注"],
        "specialty": ["专项"],
        "position": ["岗位"],
        "snapshot_date": ["更新时间"],
        # 可选生命周期列(Excel 以后补列时自动识别)
        "hire_date": ["入职日期"],
        "probation_end_date": ["试用期到期"],
        "regularization_date": ["转正日期"],
        "resignation_date": ["离职日期"],
    },
}


def clean_cell(val: Any) -> Any:
    """清洗单元格:去尾部 \\t、空白;空字符串转 None"""
    if val is None:
        return None
    if isinstance(val, str):
        v = val.replace("\t", "").strip()
        # 剥离 Excel 文本标记前导反引号(微信商户号导出的 ID 列常带 ` 前缀强制文本格式)
        v = v.lstrip("`")
        return v if v != "" else None
    # NaN 检测
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def read_csv_auto_encoding(path: Path | str, encoding: str | None = None) -> tuple[pd.DataFrame, str]:
    """自动识别编码读取 CSV,返回 (df, encoding_used)

    处理逻辑:
    1. 先按物理行读取,分割为lines
    2. 解析表头,获取预期的字段数量
    3. 逐行解析数据
    4. 如果某行的字段数少于表头字段数,说明是行不完整(中间有换行符),需要和下一行合并
    5. 如果某行的字段数多于表头字段数,说明有字段过长(包含了逗号),需要在过长字段处截断
    """
    encodings_to_try = [encoding] if encoding else ["utf-8-sig", "utf-8", "gbk", "gb18030"]
    last_err: Exception | None = None
    for enc in encodings_to_try:
        if not enc:
            continue
        try:
            with open(path, encoding=enc, newline="") as f:
                content = f.read()
            
            # 统一换行符
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            physical_lines = content.split("\n")
            
            if physical_lines and physical_lines[-1] == "":
                physical_lines.pop()
            if not physical_lines:
                return pd.DataFrame(), enc
            
            # 解析表头
            header = [str(c).replace("\t", "").strip() for c in physical_lines[0].split(',')]
            header_len = len(header)
            
            def _fix_unclosed_quote(line: str) -> str:
                """修复未闭合的引号问题
                
                场景:备注字段以双引号开头,末尾有转义双引号"",但缺少闭合引号
                导致后面所有字段都被合并到备注字段中
                
                策略:找到转义双引号""的位置,在其后面添加闭合引号
                """
                # 查找转义双引号""的位置
                pos = line.find('""')
                while pos > 0:
                    # 检查""后面是否是逗号(字段分隔符)
                    if pos + 2 < len(line) and line[pos + 2] == ',':
                        # 检查""前面是否有未闭合的引号
                        before_double_quote = line[:pos]
                        quote_count = before_double_quote.count('"')
                        if quote_count % 2 != 0:
                            # 有未闭合的引号,在""后面添加闭合引号
                            line = line[:pos + 2] + '"' + line[pos + 2:]
                    pos = line.find('""', pos + 2)
                return line
            
            def _split_fields(text: str) -> list[str]:
                """按逗号分割字段,正确处理双引号和转义双引号"""""
                fields = []
                buf = []
                in_quote = False
                i = 0
                n = len(text)
                while i < n:
                    ch = text[i]
                    if in_quote:
                        if ch == '"':
                            # 检查是否是转义的双引号("")
                            if i + 1 < n and text[i + 1] == '"':
                                buf.append('"')
                                i += 2
                                continue
                            # 闭合引号
                            in_quote = False
                        else:
                            buf.append(ch)
                        i += 1
                    else:
                        if ch == '"':
                            in_quote = True
                            i += 1
                        elif ch == ',':
                            fields.append(''.join(buf))
                            buf = []
                            i += 1
                        else:
                            buf.append(ch)
                            i += 1
                # 添加最后一个字段
                if buf:
                    fields.append(''.join(buf))
                return fields

            def _fix_unclosed_quote_in_long_field(line: str) -> str:
                """修复备注字段引号未闭合导致后续字段被吞的问题

                场景: BMS 导出 CSV 中,备注字段(投诉文本)以 " 开头但未闭合,
                后续的日期时间等字段被吞进备注,直到提交参数 JSON 里的某个 "
                被误认为闭合引号(引号总数为偶数,_fix_unclosed_quote 检测不到)。

                策略: 模拟 _split_fields 的引号状态机,在引号内找第一个
                "逗号+日期时间(yyyy-mm-dd)"模式,在逗号前补闭合引号。
                备注字段后面紧跟的是登记时间(日期时间格式),补引号后字段恢复对齐。
                """
                in_quote = False
                i = 0
                n = len(line)
                while i < n:
                    ch = line[i]
                    if in_quote:
                        if ch == '"':
                            # 转义引号 ""
                            if i + 1 < n and line[i + 1] == '"':
                                i += 2
                                continue
                            in_quote = False
                            i += 1
                        else:
                            # 在引号内,检查是否是 "逗号+日期时间" 模式
                            if ch == ',' and i + 11 <= n:
                                rest = line[i + 1:i + 11]
                                if re.match(r'\d{4}-\d{2}-\d{2}', rest):
                                    # 找到!在逗号前补闭合引号
                                    return line[:i] + '"' + line[i:]
                            i += 1
                    else:
                        if ch == '"':
                            in_quote = True
                        i += 1
                return line
            
            # 逐行解析数据
            data_rows = []
            i = 1
            while i < len(physical_lines):
                line = physical_lines[i].strip()
                if not line:
                    i += 1
                    continue
                
                # 场景1: 先修复未闭合的引号问题
                line = _fix_unclosed_quote(line)

                # 按逗号分割字段(考虑引号)
                fields = _split_fields(line)

                # 场景1.5: 字段数不足时,尝试修复"备注字段引号未闭合"
                # 必须在"和下一行合并"之前,否则会把下一行错误吞进来(导致本行错位+下一行丢失)
                # 触发条件:备注以 " 开头但未闭合,后续日期时间字段被吞,引号总数为偶数
                if len(fields) < header_len:
                    fixed_line = _fix_unclosed_quote_in_long_field(line)
                    if fixed_line != line:
                        fixed_fields = _split_fields(fixed_line)
                        if len(fixed_fields) > len(fields):
                            line = fixed_line
                            fields = fixed_fields

                # 场景2: 行不完整(字段数少于表头),需要和下一行合并
                if len(fields) < header_len:
                    # 检查下一行是否有数据
                    if i + 1 < len(physical_lines):
                        next_line = physical_lines[i + 1].strip()
                        if next_line:
                            # 合并到当前行(用空格替换换行)
                            line = line + ' ' + next_line
                            # 修复合并后的行
                            line = _fix_unclosed_quote(line)
                            fields = _split_fields(line)
                            i += 1  # 跳过已合并的行
                
                # 场景3: 字段数多于表头,合并多余的字段到最后一个字段
                # (通常是提交参数等包含JSON的字段,内部有逗号但未加引号)
                if len(fields) > header_len:
                    # 合并多余的字段到最后一个字段
                    extra_fields = fields[header_len - 1:]
                    merged_field = ','.join(extra_fields)
                    fields = fields[:header_len - 1] + [merged_field]
                
                # 补齐到表头长度
                if len(fields) < header_len:
                    fields = fields + [""] * (header_len - len(fields))
                
                data_rows.append(fields)
                i += 1
            
            df = pd.DataFrame(data_rows, columns=header, dtype=str)
            return df, enc
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
            continue
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f"无法识别CSV编码: {last_err}")


def read_table(path: Path | str, sheet_name: str | None = None, encoding: str | None = None) -> tuple[pd.DataFrame, str | None, list[str] | None]:
    """读取表格文件,返回 (df, used_encoding, sheet_names)

    支持 .csv / .xlsx / .xls

    注意:
    - CSV 文件:自动修复单元格内换行导致的记录断裂
    - XLSX 文件:读取后检查是否有空行(首列为空但其他列有值),合并到上一行
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df, enc = read_csv_auto_encoding(path, encoding)
        # 清洗列名(去 \t)
        df.columns = [str(c).replace("\t", "").strip() for c in df.columns]
        return df, enc, None
    elif suffix in (".xlsx", ".xls"):
        # 优先 calamine 引擎(对样式异常的 xlsx 更稳健),失败回退 openpyxl
        try:
            xl = pd.ExcelFile(path, engine="calamine")
        except Exception:
            xl = pd.ExcelFile(path)
        sheets = xl.sheet_names
        target = sheet_name if sheet_name in sheets else (sheets[0] if sheets else None)
        try:
            df = pd.read_excel(path, sheet_name=target, dtype=str, engine="calamine")
        except Exception:
            df = pd.read_excel(path, sheet_name=target, dtype=str)
        df.columns = [str(c).replace("\t", "").strip() for c in df.columns]

        # 修复xlsx中可能存在的单元格内换行导致的记录断裂
        # 如果某行首列为空但其他列有值,视为上一行的续行
        df = _fix_xlsx_line_breaks(df)

        return df, None, sheets
    else:
        raise ValueError(f"不支持的文件类型: {suffix}")


def _fix_xlsx_line_breaks(df: pd.DataFrame) -> pd.DataFrame:
    """修复xlsx中单元格内换行导致的记录断裂

    某些导出工具生成的xlsx中,单元格内的换行符会导致pandas将一行拆成多行,
    表现为:后续行首列为空但其他列有值。
    """
    if df.empty or len(df.columns) == 0:
        return df

    rows = df.to_dict(orient="records")
    fixed_rows = []
    i = 0
    while i < len(rows):
        row = rows[i]
        first_col_value = str(row[df.columns[0]]).strip() if df.columns[0] in row else ""

        if not first_col_value and i > 0:
            # 首列为空且不是第一行 -> 续行,合并到上一行
            prev_row = fixed_rows[-1]
            for col in df.columns:
                curr_val = str(row[col]).strip() if row.get(col) else ""
                prev_val = str(prev_row[col]).strip() if prev_row.get(col) else ""
                if curr_val:
                    if prev_val:
                        prev_row[col] = prev_val + "\n" + curr_val
                    else:
                        prev_row[col] = curr_val
        else:
            fixed_rows.append(row.copy())
        i += 1

    return pd.DataFrame(fixed_rows, columns=df.columns)


def suggest_mapping(data_type: str, columns: list[str]) -> dict[str, str]:
    """根据列模板自动建议列映射(目标字段 -> 实际列名)

    增强匹配策略:
    1. 精确匹配(完全相同)
    2. 关键词包含匹配(目标字段的候选名出现在源列名中)
    3. 反向匹配(源列名出现在候选名中)
    4. 拼音首字母匹配(针对英文/拼音混合列名)
    """
    template = COLUMN_TEMPLATES.get(data_type, {})
    mapping: dict[str, str] = {}
    used_cols: set[str] = set()

    # 预处理列名:去空格、去特殊字符、转小写
    def normalize(s: str) -> str:
        return re.sub(r'[\s\t_/-]', '', s).lower()

    normalized_cols = [(c, normalize(c)) for c in columns]

    # 优先级1: 精确匹配
    for target_field, candidates in template.items():
        for cand in candidates:
            cand_norm = normalize(cand)
            for col, col_norm in normalized_cols:
                if col_norm == cand_norm and col not in used_cols:
                    mapping[target_field] = col
                    used_cols.add(col)
                    break
            if target_field in mapping:
                break

    # 优先级2: 候选名包含在源列名中
    for target_field, candidates in template.items():
        if target_field in mapping:
            continue
        for cand in candidates:
            cand_norm = normalize(cand)
            for col, col_norm in normalized_cols:
                if cand_norm in col_norm and col not in used_cols:
                    mapping[target_field] = col
                    used_cols.add(col)
                    break
            if target_field in mapping:
                break

    # 优先级3: 源列名包含在候选名中
    for target_field, candidates in template.items():
        if target_field in mapping:
            continue
        for cand in candidates:
            cand_norm = normalize(cand)
            for col, col_norm in normalized_cols:
                if col_norm in cand_norm and col not in used_cols:
                    mapping[target_field] = col
                    used_cols.add(col)
                    break
            if target_field in mapping:
                break

    return mapping


def extract_refund_no_from_remark(remark: str | None) -> str | None:
    """从钱包提现备注中正则提取退费单号 RF..."""
    if not remark:
        return None
    m = re.search(r"RF\d{10,}", remark)
    return m.group(0) if m else None


def parse_file(path: Path | str, data_type: str, sheet_name: str | None = None, encoding: str | None = None) -> dict[str, Any]:
    """解析文件,返回预览数据

    Returns:
        dict: columns, sample_rows, total_rows, detected_encoding, suggested_mapping, sheet_names
    """
    df, used_enc, sheet_names = read_table(path, sheet_name=sheet_name, encoding=encoding)
    # 清洗所有单元格(pandas 2.1+ 用 DataFrame.map 替代 applymap)
    df = df.map(clean_cell)  # noqa: PD003
    total_rows = len(df)
    sample = df.head(10).to_dict(orient="records")
    # 转 JSON 安全类型
    sample = [_json_safe(r) for r in sample]
    suggested = suggest_mapping(data_type, list(df.columns))
    return {
        "columns": list(df.columns),
        "sample_rows": sample,
        "total_rows": total_rows,
        "detected_encoding": used_enc,
        "suggested_mapping": suggested,
        "sheet_names": sheet_names,
    }


def _json_safe(row: dict) -> dict:
    """将行数据转为 JSON 安全类型"""
    out = {}
    for k, v in row.items():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            out[k] = None
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def load_dataframe(path: Path | str, sheet_name: str | None = None, encoding: str | None = None) -> pd.DataFrame:
    """加载完整 DataFrame(供 upsert 使用),单元格已清洗"""
    df, _, _ = read_table(path, sheet_name=sheet_name, encoding=encoding)
    df = df.map(clean_cell)  # noqa: PD003
    return df


# 上传文件扩展名白名单(P0-3: 拒绝 html/svg/exe 等, 防存储型 XSS 与可执行文件)
ALLOWED_UPLOAD_EXT = {".csv", ".xlsx", ".xls"}
# magic bytes 校验: 防止改扩展名伪装(xlsx=ZIP, xls=OLE2 复合文档; csv 为文本无固定头)
_UPLOAD_MAGIC: dict[str, bytes] = {
    ".xlsx": b"PK\x03\x04",
    ".xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
}


def _validate_upload_content(content: bytes, filename: str) -> str:
    """上传内容校验(P0-3): 扩展名白名单 + magic bytes + 大小上限, 落盘前拒绝非法文件。

    返回规范化扩展名(小写); 校验失败抛 ValueError, 由调用方转 400。
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise ValueError(
            f"不支持的文件类型 {ext or '(无扩展名)'}, 仅允许 {sorted(ALLOWED_UPLOAD_EXT)}"
        )
    if len(content) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise ValueError(f"文件超过 {settings.MAX_UPLOAD_SIZE_MB}MB 限制")
    if ext == ".csv":
        # CSV 为文本: 前 4KB 不应含 NUL 字节(二进制伪装特征)
        if b"\x00" in content[:4096]:
            raise ValueError("CSV 文件含二进制内容, 疑似伪装")
    else:
        magic = _UPLOAD_MAGIC[ext]
        if not content.startswith(magic):
            raise ValueError(f"{ext} 文件内容与扩展名不匹配(magic bytes 校验失败)")
    return ext


def save_upload_file(content: bytes, filename: str) -> tuple[Path, str]:
    """保存上传文件到 uploads/yyyy-MM/原文件名,返回 (绝对路径, 相对路径 stored_path)

    按月归档便于存档追溯；同名文件加时间戳后缀避免覆盖。
    P0-3: 落盘前先做扩展名白名单 + magic bytes + 大小校验, 拒绝 html/svg/exe 等。
    """
    from datetime import datetime

    # 落盘前校验(原实现先落盘后由 parse_file 校验, .html 会残留在 /uploads 下)
    _validate_upload_content(content, filename)

    now = datetime.now()
    month_dir = now.strftime("%Y-%m")
    upload_dir = Path(settings.UPLOAD_DIR) / month_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    # 防止路径穿越
    safe_name = Path(filename).name
    dest = upload_dir / safe_name
    if dest.exists():
        # 同名加时间戳后缀,保留所有历史版本
        stem = Path(safe_name).stem
        suffix = Path(safe_name).suffix
        ts = now.strftime("%Y%m%d%H%M%S")
        dest = upload_dir / f"{stem}_{ts}{suffix}"
    with open(dest, "wb") as f:
        f.write(content)
    stored_path = f"{month_dir}/{dest.name}"  # 相对 UPLOAD_DIR
    return dest, stored_path
