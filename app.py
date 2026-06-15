# -*- coding: utf-8 -*-
"""
源网荷储一体化计算平台 - Web版
Flask 主应用程序
"""

import os, sys, json, threading, traceback, uuid, datetime, gc, io
from pathlib import Path
from functools import wraps

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for,
    flash, send_file, session, make_response
)
from flask_login import (
    LoginManager, login_user, logout_user, login_required,
    current_user
)

from config import Config
from models import db, User, ComputeTask

# ============================================================
# Flask App 初始化
# ============================================================
app = Flask(__name__)
app.config.from_object(Config)

db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = '请先登录后再访问此页面。'

# 确保数据库和默认管理员就绪（Railway部署时自动执行）
with app.app_context():
    db.create_all()
    admin = User.query.filter_by(is_admin=True).first()
    if not admin:
        admin = User(username='admin', phone='13001080740', is_admin=True)
        admin.set_password('0813@Ming')
        db.session.add(admin)
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ============================================================
# 装饰器
# ============================================================
def admin_required(f):
    """管理员权限装饰器"""
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            flash('需要管理员权限', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# ============================================================
# 验证码管理（内存存储，5分钟过期）
# ============================================================
import random, time as _time_module
_verify_codes = {}  # {phone: {code, expires_at}}

def _generate_code():
    return str(random.randint(100000, 999999))

def _store_code(phone):
    code = _generate_code()
    _verify_codes[phone] = {"code": code, "expires": _time_module.time() + 300}
    # 打印到控制台（后续替换为真实短信API）
    print(f"\n{'='*40}\n  [验证码] {phone} -> {code}\n{'='*40}\n")
    return code

def _verify_code(phone, code):
    entry = _verify_codes.get(phone)
    if not entry:
        return False
    if _time_module.time() > entry["expires"]:
        del _verify_codes[phone]
        return False
    if entry["code"] == code:
        del _verify_codes[phone]
        return True
    return False

# ============================================================
# 发送验证码API
# ============================================================
@app.route('/api/send-code', methods=['POST'])
def api_send_code():
    data = request.get_json()
    phone = data.get('phone', '').strip()
    if not phone or len(phone) != 11 or not phone.isdigit():
        return jsonify({"error": "手机号格式错误"}), 400
    code = _store_code(phone)
    return jsonify({"success": True, "code": code})  # 返回验证码方便测试（生产环境去掉code字段）

# ============================================================
# 首页 - 带动画的旋转元素页面
# ============================================================
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# ============================================================
# 用户注册
# ============================================================
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        verify_code = request.form.get('verify_code', '').strip()
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        agree = request.form.get('agree')
        
        errors = []
        if not phone or len(phone) != 11 or not phone.isdigit():
            errors.append('请输入正确的11位手机号')
        if not verify_code:
            errors.append('请输入短信验证码')
        elif not _verify_code(phone, verify_code):
            errors.append('验证码错误或已过期，请重新获取')
        if not password or len(password) < 6:
            errors.append('密码至少6位')
        if password != confirm_password:
            errors.append('两次密码不一致')
        if not agree:
            errors.append('请同意用户协议')
        
        # 手机号就是用户名，检查是否已注册
        if User.query.filter_by(phone=phone).first():
            errors.append('该手机号已被注册')
        
        if errors:
            return render_template('register.html', errors=errors, phone=phone)
        
        # 创建用户 - 第一个注册的用户(或手机号13001080740)设为管理员
        is_first = User.query.count() == 0
        is_admin = is_first or phone == '13001080740'
        
        user = User(username=phone, phone=phone, is_admin=is_admin)
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        login_user(user)
        flash('注册成功！欢迎使用源网荷储一体化计算平台。', 'success')
        return redirect(url_for('dashboard'))
    
    return render_template('register.html')

# ============================================================
# 用户登录
# ============================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        login_id = request.form.get('login_id', '').strip()  # 用户名或手机号
        password = request.form.get('password', '').strip()
        
        # 支持手机号登录（用户名=手机号）
        user = User.query.filter(
            (User.username == login_id) | (User.phone == login_id)
        ).first()
        
        if user and user.check_password(password):
            if not user.is_active:
                flash('该账号已被禁用，请联系管理员。', 'error')
                return render_template('login.html')
            
            login_user(user, remember=request.form.get('remember'))
            user.last_login = datetime.datetime.utcnow()
            db.session.commit()
            
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('dashboard'))
        else:
            flash('用户名/手机号或密码错误。', 'error')
    
    return render_template('login.html')

# ============================================================
# 退出登录
# ============================================================
@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('已退出登录。', 'info')
    return redirect(url_for('index'))

# ============================================================
# 文件上传（保存到用户本地目录）
# ============================================================
import datetime as _dt
from werkzeug.utils import secure_filename

USER_DATA_ROOT = Path(r"C:\Users\dukm6\Desktop\power\users")

@app.route('/api/upload', methods=['POST'])
@login_required
def api_upload():
    if 'file' not in request.files:
        return jsonify({"error": "未选择文件"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "未选择文件"}), 400
    
    ftype = request.form.get('type', 'load')
    phone = current_user.phone
    user_dir = USER_DATA_ROOT / phone
    user_dir.mkdir(parents=True, exist_ok=True)
    
    ext = Path(file.filename).suffix or '.xlsx'
    save_path = user_dir / f"{ftype}_{_dt.datetime.now().strftime('%Y%m%d_%H%M%S')}{ext}"
    file.save(str(save_path))
    
    return jsonify({"success": True, "path": str(save_path), "filename": file.filename})

@app.route('/api/download-template')
@login_required
def api_download_template():
    """下载负荷模板Excel"""
    import pandas as pd
    buf = io.BytesIO()
    df = pd.DataFrame({'Time': pd.date_range('2023-01-01', periods=8760, freq='H'), 'Load': 2000.0})
    df.to_excel(buf, index=False)
    buf.seek(0)
    return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                     as_attachment=True, download_name='template_load.xlsx')

@app.route('/api/price-groups', methods=['GET', 'POST'])
@login_required
def api_price_groups():
    """获取或更新分时电价群组"""
    if request.method == 'GET':
        pg = {
            "一组": {'months':[1,11,12],'prices':[0.417,0.1251,0.1251,0.1251,0.1251,0.1251,0.1251,0.417,0.7089,0.7089,0.417,0.417,0.417,0.10008,0.10008,0.10008,0.417,0.7089,0.85068,0.85068,0.7089,0.7089,0.7089,0.417]},
            "二组": {'months':[2],'prices':[0.417,0.1251,0.1251,0.1251,0.1251,0.1251,0.417,0.417,0.417,0.417,0.417,0.417,0.417,0.10008,0.10008,0.10008,0.417,0.7089,0.7089,0.7089,0.7089,0.7089,0.7089,0.417]},
            "三组": {'months':[3,4,5,9,10],'prices':[0.417,0.1251,0.1251,0.1251,0.1251,0.1251,0.417,0.417,0.417,0.417,0.417,0.417,0.417,0.1251,0.1251,0.1251,0.417,0.7089,0.7089,0.7089,0.7089,0.7089,0.7089,0.417]},
            "四组": {'months':[6,7,8],'prices':[0.1251,0.1251,0.1251,0.1251,0.1251,0.1251,0.1251,0.417,0.417,0.417,0.7089,0.7089,0.417,0.417,0.417,0.417,0.7089,0.7089,0.85068,0.85068,0.85068,0.7089,0.417,0.1251]}
        }
        return jsonify(pg)
    else:
        data = request.get_json()
        return jsonify({"success": True, "message": "电价群组已更新"})

@app.route('/api/output-dir', methods=['POST'])
@login_required
def api_output_dir():
    """设置输出文件夹"""
    data = request.get_json()
    path = data.get('path', str(USER_DATA_ROOT / current_user.phone))
    return jsonify({"path": str(USER_DATA_ROOT / current_user.phone), "message": f"输出文件夹: {path}"})

@app.route('/api/user-data', methods=['GET'])
@login_required
def api_user_data():
    user_dir = USER_DATA_ROOT / current_user.phone
    if not user_dir.exists():
        return jsonify({"files": [], "tasks": []})
    files = sorted(
        [{"name": f.name, "time": _dt.datetime.fromtimestamp(f.stat().st_mtime).strftime('%m-%d %H:%M'), "size": f'{f.stat().st_size/1024:.1f}KB'}
         for f in user_dir.iterdir() if f.is_file()],
        key=lambda x: x['time'], reverse=True
    )[:20]
    tasks = sorted(
        [{"name": d.name, "time": _dt.datetime.fromtimestamp(d.stat().st_mtime).strftime('%m-%d %H:%M')}
         for d in user_dir.iterdir() if d.is_dir()],
        key=lambda x: x['time'], reverse=True
    )[:20]
    return jsonify({"files": files, "tasks": tasks})

# ============================================================
# 找回密码
# ============================================================
@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        phone = request.form.get('phone', '').strip()
        verify_code = request.form.get('verify_code', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        errors = []
        if not phone or len(phone) != 11 or not phone.isdigit():
            errors.append('请输入正确的11位手机号')
        if not verify_code:
            errors.append('请输入短信验证码')
        elif not _verify_code(phone, verify_code):
            errors.append('验证码错误或已过期，请重新获取')
        if not new_password or len(new_password) < 6:
            errors.append('新密码至少6位')
        if new_password != confirm_password:
            errors.append('两次密码不一致')
        
        user = User.query.filter_by(phone=phone).first()
        if not user:
            errors.append('该手机号未注册，请先注册')
        
        if errors:
            return render_template('forgot_password.html', errors=errors, phone=phone)
        
        user.set_password(new_password)
        db.session.commit()
        flash('密码重置成功！请使用新密码登录。', 'success')
        return redirect(url_for('login'))
    
    return render_template('forgot_password.html')

# ============================================================
# 用户协议
# ============================================================
@app.route('/agreement')
def agreement():
    return render_template('agreement.html')

# ============================================================
# 用户仪表盘（计算平台主页）
# ============================================================
@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html')

# ============================================================
# 荷-储协同计算 (S1)
# ============================================================
@app.route('/compute/s1')
@login_required
def compute_s1():
    return render_template('compute_s1.html')

# ============================================================
# 电-荷-储协同计算 (S2)
# ============================================================
@app.route('/compute/s2')
@login_required
def compute_s2():
    return render_template('compute_s2.html')

# ============================================================
# 算-电-荷-储协同 (开发中)
# ============================================================
@app.route('/compute/s3')
@login_required
def compute_s3():
    return render_template('compute_s3.html')

# ============================================================
# API: 执行计算任务
# ============================================================
@app.route('/api/compute/start', methods=['POST'])
@login_required
def api_start_compute():
    """启动计算任务"""
    data = request.get_json()
    task_type = data.get('task_type', 's1')
    
    if task_type not in ('s1', 's2'):
        return jsonify({'error': '无效的计算类型'}), 400
    
    # 创建任务记录
    task = ComputeTask(
        user_id=current_user.id,
        task_type=task_type,
        status='pending',
        params=json.dumps(data.get('params', {}))
    )
    db.session.add(task)
    db.session.commit()
    
    # 异步执行计算
    thread = threading.Thread(
        target=_run_compute_task,
        args=(task.id, task_type, data.get('params', {})),
        daemon=True
    )
    thread.start()
    
    return jsonify({'task_id': task.id, 'status': 'started'})

def _run_compute_task(task_id, task_type, params):
    """后台执行计算任务"""
    with app.app_context():
        task = ComputeTask.query.get(task_id)
        if not task:
            return
        
        task.status = 'running'
        db.session.commit()
        
        try:
            # 根据任务类型执行不同计算
            if task_type == 's1':
                result = _run_s1_compute(task, params)
            else:
                result = _run_s2_compute(task, params)
            
            if result:
                task.status = 'completed'
                task.progress = 100
                task.result_file = result
                task.completed_at = datetime.datetime.utcnow()
            else:
                task.status = 'failed'
                task.error_message = '计算未产生结果'
            
        except Exception as e:
            task.status = 'failed'
            task.error_message = str(e)
            traceback.print_exc()
        
        db.session.commit()

def _run_s1_compute(task, params):
    """荷-储协同计算"""
    try:
        import numpy as np
        import pandas as pd
        from pathlib import Path
        
        # 从数据库获取当前用户
        user = User.query.get(task.user_id)
        user_dir = USER_DATA_ROOT / user.phone if user else None
        
        # 读取参数
        battery_cap = float(params.get('battery_capacity_mw', 3))
        unit_invest = float(params.get('unit_investment', 700))
        annual_om = float(params.get('annual_om_cost', 42000))
        capacity_price = float(params.get('capacity_price', 30))
        charge_eff = float(params.get('charge_efficiency', 0.95))
        discharge_eff = float(params.get('discharge_efficiency', 0.95))
        max_c_rate = float(params.get('max_c_rate', 0.5))
        
        # 尝试加载用户上传的负荷文件
        load_data = None
        if user_dir and user_dir.exists():
            load_files = sorted(user_dir.glob('load_*.xlsx'), key=lambda x: x.stat().st_mtime, reverse=True)
            if load_files:
                try:
                    df = pd.read_excel(load_files[0])
                    if 'Load' in df.columns:
                        load_data = df['Load'].values[:8760]
                    elif len(df.columns) >= 2:
                        load_data = df.iloc[:, 1].values[:8760]
                    task.error_message = f'使用文件: {load_files[0].name}'
                except Exception as e:
                    task.error_message = f'文件读取失败: {e}'
        
        # 未找到文件则生成模拟数据
        if load_data is None:
            hours = 8760
            t = np.arange(hours)
            load = 2000 * (0.3 * np.sin(2*np.pi*t/24) + 0.7) * (0.1 * np.sin(2*np.pi*t/168) + 0.9) * (0.2 * np.sin(2*np.pi*t/8760) + 0.8)
            load = np.abs(load + np.random.normal(0, 200, hours))
            task.error_message = '未找到负荷文件，使用模拟数据'
        else:
            load = np.abs(load_data)
            hours = len(load)
        
        # 分时电价
        prices = np.ones(hours) * 0.6
        for h in range(hours):
            hour = h % 24
            if 9 <= hour <= 11 or 18 <= hour <= 20:
                prices[h] = 0.85
            elif 0 <= hour <= 7:
                prices[h] = 0.13
        
        # 简单储能策略
        cap_kw = battery_cap * 1000
        soc = np.zeros(hours)
        power = np.zeros(hours)
        soc_current = cap_kw * 0.1
        
        # 每天充放电策略
        for day in range(hours // 24):
            start = day * 24
            end = min(start + 24, hours)
            day_prices = prices[start:end]
            day_load = load[start:end]
            
            # 找最低价时段充电
            low_hours = np.argsort(day_prices)[:4]
            high_hours = np.argsort(day_prices)[-4:]
            
            for h in range(end - start):
                idx = start + h
                if h in low_hours:
                    chg = min(cap_kw * 0.5, (cap_kw * 0.95 - soc_current) / 0.95)
                    power[idx] = chg
                    soc_current += chg * 0.95
                elif h in high_hours:
                    dis = min(cap_kw * 0.5, (soc_current - cap_kw * 0.1) * 0.95)
                    power[idx] = -dis
                    soc_current -= dis / 0.95
                else:
                    power[idx] = 0
                
                soc[idx] = soc_current / cap_kw
        
        # 计算经济效益
        elec_profit = sum(-power[i] * prices[i] for i in range(hours))
        cap_saving = 0
        for m in range(12):
            m_start = m * 730
            m_end = min((m+1) * 730, hours)
            orig_max = max(load[m_start:m_end])
            new_max = max(load[m_start:m_end] + power[m_start:m_end])
            if new_max < orig_max:
                cap_saving += (orig_max - new_max) * 30
        
        first_year_profit = elec_profit + cap_saving - annual_om
        investment = unit_invest * cap_kw
        payback = investment / first_year_profit if first_year_profit > 0 else float('inf')
        
        # 保存结果到用户专属目录
        import datetime as _dt
        result_dir = Path(r'C:\Users\dukm6\Desktop\power\users') / current_user.phone / f's1_{task.id}_{_dt.datetime.now().strftime("%Y%m%d_%H%M%S")}'
        result_dir.mkdir(parents=True, exist_ok=True)
        
        df = pd.DataFrame({
            'Hour': range(hours),
            'Load_Original': load,
            'Load_New': load + power,
            'Power': power,
            'SOC': soc,
            'Price': prices,
            'Electricity_Profit': -power * prices,
        })
        
        df.to_csv(result_dir / 'result.csv', index=False, encoding='utf-8-sig')
        
        with open(result_dir / 'summary.txt', 'w', encoding='utf-8') as f:
            f.write(f"=== 荷-储协同计算结果 ===\n")
            f.write(f"电池容量: {battery_cap} MW\n")
            f.write(f"首年综合盈利: {first_year_profit:,.2f} 元\n")
            f.write(f"电价套利: {elec_profit:,.2f} 元\n")
            f.write(f"需量节省: {cap_saving:,.2f} 元\n")
            f.write(f"投资回收期: {payback:.2f} 年\n")
        
        return str(result_dir)
        
    except Exception as e:
        traceback.print_exc()
        raise e

def _run_s2_compute(task, params):
    """执行电-荷-储协同计算"""
    try:
        import numpy as np
        import pandas as pd
        from pathlib import Path
        
        battery_cap = float(params.get('battery_capacity_mw', 3))
        pv_cap = float(params.get('pv_capacity_mw', 45))
        wind_cap = float(params.get('wind_capacity_mw', 10))
        annual_om = float(params.get('annual_om_cost', 42000))
        
        hours = 8760
        t = np.arange(hours)
        
        # 负荷
        load = 2000 * (0.3 * np.sin(2*np.pi*t/24) + 0.7) * (0.1 * np.sin(2*np.pi*t/168) + 0.9) * (0.2 * np.sin(2*np.pi*t/8760) + 0.8)
        load = np.abs(load + np.random.normal(0, 200, hours))
        
        # 光伏出力（白天）
        pv = np.zeros(hours)
        for h in range(hours):
            hour = h % 24
            if 6 <= hour <= 18:
                pv[h] = pv_cap * 1000 * max(0, np.sin(np.pi * (hour - 6) / 12)) * (0.7 + 0.3 * np.random.random())
        
        # 风电出力
        wind = np.zeros(hours)
        for h in range(hours):
            wind[h] = wind_cap * 1000 * abs(0.3 + 0.2 * np.sin(2*np.pi*h/48) + 0.1 * np.random.randn())
        
        green = pv + wind
        net_load = np.maximum(0, load - green)
        
        # 电价
        prices = np.ones(hours) * 0.6
        for h in range(hours):
            hour = h % 24
            if 9 <= hour <= 11 or 18 <= hour <= 20:
                prices[h] = 0.85
            elif 0 <= hour <= 7:
                prices[h] = 0.13
        
        # 储能策略
        cap_kw = battery_cap * 1000
        soc = np.zeros(hours)
        power = np.zeros(hours)
        soc_current = cap_kw * 0.1
        
        for day in range(hours // 24):
            start = day * 24
            end = min(start + 24, hours)
            day_prices = prices[start:end]
            day_net = net_load[start:end]
            
            low_hours = np.argsort(day_prices)[:4]
            high_hours = np.argsort(day_prices)[-4:]
            
            for h in range(end - start):
                idx = start + h
                if h in low_hours:
                    chg = min(cap_kw * 0.5, (cap_kw * 0.95 - soc_current) / 0.95)
                    power[idx] = chg
                    soc_current += chg * 0.95
                elif h in high_hours:
                    dis = min(cap_kw * 0.5, (soc_current - cap_kw * 0.1) * 0.95)
                    power[idx] = -dis
                    soc_current -= dis / 0.95
                else:
                    power[idx] = 0
                
                soc[idx] = soc_current / cap_kw
        
        # 经济指标
        elec_profit = sum(-power[i] * prices[i] for i in range(hours))
        grid_purchase_base = sum(load[i] * 0.6 for i in range(hours))
        grid_purchase_actual = sum(max(0, net_load[i] + power[i]) * prices[i] for i in range(hours))
        purchase_saving = grid_purchase_base - grid_purchase_actual
        green_consumption = sum(min(green[i], load[i]) for i in range(hours))
        
        cap_saving = 0
        for m in range(12):
            m_start = m * 730
            m_end = min((m+1) * 730, hours)
            orig_max = max(net_load[m_start:m_end])
            new_max = max(net_load[m_start:m_end] + power[m_start:m_end])
            if new_max < orig_max:
                cap_saving += (orig_max - new_max) * 30
        
        investment = 700 * cap_kw
        first_year_profit = elec_profit + cap_saving + purchase_saving * 0.3 - annual_om
        payback = investment / first_year_profit if first_year_profit > 0 else float('inf')
        
        # 保存结果到用户专属目录
        import datetime as _dt
        result_dir = Path(r'C:\Users\dukm6\Desktop\power\users') / current_user.phone / f's2_{task.id}_{_dt.datetime.now().strftime("%Y%m%d_%H%M%S")}'
        result_dir.mkdir(parents=True, exist_ok=True)
        
        df = pd.DataFrame({
            'Hour': range(hours),
            'Load_Original': load,
            'PV_Output': pv,
            'Wind_Output': wind,
            'Green_Supply': green,
            'Net_Load': net_load,
            'Load_New': net_load + power,
            'Power': power,
            'SOC': soc,
            'Price': prices,
            'Electricity_Profit': -power * prices,
            'Grid_Purchase': np.maximum(0, net_load + power),
        })
        
        df.to_csv(result_dir / 'result.csv', index=False, encoding='utf-8-sig')
        
        with open(result_dir / 'summary.txt', 'w', encoding='utf-8') as f:
            f.write(f"=== 电-荷-储协同计算结果 ===\n")
            f.write(f"储能套利: {elec_profit:,.2f} 元\n")
            f.write(f"需量节省: {cap_saving:,.2f} 元\n")
            f.write(f"购电节省: {purchase_saving:,.2f} 元\n")
            f.write(f"首年盈利: {first_year_profit:,.2f} 元\n")
            f.write(f"回收期: {payback:.2f} 年\n")
            f.write(f"绿电消纳量: {green_consumption/1000:.1f} MWh\n")
        
        return str(result_dir)
        
    except Exception as e:
        traceback.print_exc()
        raise e

# ============================================================
# API: 查询任务状态
# ============================================================
@app.route('/api/task/<int:task_id>/status')
@login_required
def api_task_status(task_id):
    task = ComputeTask.query.get(task_id)
    if not task or task.user_id != current_user.id:
        return jsonify({'error': '任务不存在'}), 404
    
    return jsonify(task.to_dict())

# ============================================================
# API: 下载结果
# ============================================================
@app.route('/api/task/<int:task_id>/download')
@login_required
def api_task_download(task_id):
    task = ComputeTask.query.get(task_id)
    if not task or task.user_id != current_user.id:
        return jsonify({'error': '任务不存在'}), 404
    
    if task.status != 'completed' or not task.result_file:
        return jsonify({'error': '结果不可用'}), 400
    
    import zipfile, io
    
    result_dir = Path(task.result_file)
    if not result_dir.exists():
        return jsonify({'error': '结果文件丢失'}), 404
    
    # 创建ZIP
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in result_dir.iterdir():
            if f.is_file():
                zf.write(f, f.name)
    
    memory_file.seek(0)
    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'computation_result_{task_id}.zip'
    )

# ============================================================
# API: 用户任务列表
# ============================================================
@app.route('/api/tasks')
@login_required
def api_tasks():
    tasks = ComputeTask.query.filter_by(user_id=current_user.id)\
        .order_by(ComputeTask.created_at.desc()).limit(20).all()
    return jsonify([t.to_dict() for t in tasks])

# ============================================================
# 管理员页面
# ============================================================
@app.route('/admin')
@admin_required
def admin_panel():
    users = User.query.all()
    tasks = ComputeTask.query.order_by(ComputeTask.created_at.desc()).limit(50).all()
    return render_template('admin.html', users=users, tasks=tasks)

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def api_admin_users():
    users = User.query.all()
    return jsonify([u.to_dict() for u in users])

@app.route('/api/admin/user/<int:user_id>/toggle', methods=['POST'])
@admin_required
def api_toggle_user(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    if user.id == current_user.id:
        return jsonify({'error': '不能操作自己的账号'}), 400
    
    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({'success': True, 'is_active': user.is_active})

# ============================================================
# 错误处理
# ============================================================
@app.errorhandler(404)
def not_found(e):
    return render_template('index.html', error='页面未找到'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('index.html', error='服务器内部错误'), 500

# ============================================================
# 启动
# ============================================================
def init_db():
    """初始化数据库"""
    with app.app_context():
        db.create_all()
        # 检查是否需要创建管理员
        admin = User.query.filter_by(is_admin=True).first()
        if not admin:
            admin = User(
                username='admin',
                phone='13001080740',
                is_admin=True
            )
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("默认管理员已创建: admin / admin123")
            print("请在首次登录后修改密码！")

if __name__ == '__main__':
    init_db()
    print("=" * 60)
    print("源网荷储一体化计算平台 Web版")
    print(f"访问地址: http://127.0.0.1:5000")
    print(f"管理员账号: admin / admin123")
    print("=" * 60)
    app.run(debug=True, host='0.0.0.0', port=5000)
