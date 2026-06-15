# -*- coding: utf-8 -*-
"""
源网荷储一体化计算平台 - Web版 v1.1
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

# 导入原始计算引擎（避免tkinter GUI启动）
import warnings as _w
_w.filterwarnings('ignore')
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
_CURRENT_DIR = Path(__file__).resolve().parent
_ENGINE_GLOBALS = {}
with open(_CURRENT_DIR.parent / '0612-协同1.0调试.py', 'r', encoding='utf-8') as _f:
    _engine_source = _f.read()
_engine_source = _engine_source.replace("if __name__==\"__main__\":", "if False:")
exec(_engine_source, _ENGINE_GLOBALS)

# 计算前重新加载引擎（确保使用最新代码）
def _reload_engine():
    """重新读取并执行原始代码，确保计算使用最新修改"""
    global _ENGINE_GLOBALS
    with open(_CURRENT_DIR.parent / '0612-协同1.0调试.py', 'r', encoding='utf-8') as _f:
        _src = _f.read()
    _src = _src.replace("if __name__==\"__main__\":", "if False:")
    _new_globals = {}
    exec(_src, _new_globals)
    _ENGINE_GLOBALS = _new_globals

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
    """荷-储协同计算 — 使用原始计算引擎"""
    try:
        _reload_engine()  # 每次计算前重新加载最新代码
        import numpy as np
        import pandas as pd
        import datetime as _dt
        
        C = _ENGINE_GLOBALS['C']
        U = _ENGINE_GLOBALS['U']
        IdealStrat = _ENGINE_GLOBALS['IdealStrat']
        HybridForecaster = _ENGINE_GLOBALS['HybridForecaster']
        TransformerForecaster = _ENGINE_GLOBALS.get('TransformerForecaster')
        RollingStratOnline = _ENGINE_GLOBALS['RollingStratOnline']
        econ = _ENGINE_GLOBALS['econ']
        
        user = User.query.get(task.user_id)
        user_dir = USER_DATA_ROOT / user.phone if user else USER_DATA_ROOT / 'default'
        user_dir.mkdir(parents=True, exist_ok=True)
        
        # 读取参数
        strategy = params.get('strategy', 'MILP')
        p = C.DP.copy()
        for k in ['battery_capacity_mw','unit_investment','annual_om_cost','capacity_price',
                  'charge_efficiency','discharge_efficiency','dod','cycle_life','max_c_rate','discount_rate']:
            if k in params:
                p[k] = float(params[k])
        p['soc_min'] = max(p.get('soc_min', 0.1), 0.05)
        p['soc_max'] = min(0.95, p.get('soc_min', 0.1) + p.get('dod', 0.85))
        
        # 加载负荷数据
        load_data = None
        load_files = sorted(user_dir.glob('load_*.xlsx'), key=lambda x: x.stat().st_mtime, reverse=True)
        if load_files:
            df = pd.read_excel(load_files[0])
            if 'Load' in df.columns:
                load_data = df['Load'].values
            elif len(df.columns) >= 2:
                load_data = df.iloc[:, 1].values
        
        if load_data is None:
            # 使用工具函数生成模拟负荷
            load_df = U.gen_load()
            load = load_df['Load'].values[:8760]
        else:
            load = np.abs(load_data)[:8760]
            load = np.resize(load, 8760) if len(load) < 8760 else load[:8760]
        
        # 构建分时电价
        pg = C.PG.copy()
        prices = U.build_price(pg)
        prices = prices[:8760]
        
        task.progress = 10
        db.session.commit()
        
        if strategy == 'MILP':
            # MILP理想最优策略
            strat = IdealStrat(p)
            df = strat.run(load, prices, 
                          lambda cur, tot, msg: setattr(task, 'progress', min(99, 10 + int(cur/tot*89))) or db.session.commit(), 
                          threading.Event(), threading.Event())
        elif strategy in ('Hybrid', 'Transformer'):
            # AI预测策略
            sh = int(params.get('sim_hours', 4380))
            s0 = int(params.get('start_hour', 0))
            hh = int(params.get('train_hours', 336))
            fi = int(params.get('ft_interval', 1))
            pred_len = int(params.get('pred_len', 1))
            
            e0 = min(8759, s0 + sh - 1)
            ls = load[s0:e0+1]
            ps = prices[s0:e0+1]
            
            if strategy == 'Hybrid':
                forecaster_class = HybridForecaster
            else:
                forecaster_class = TransformerForecaster
            
            strat = RollingStratOnline(p, forecaster_class, hh, fi, pred_len, 
                                       name=f"{strategy}Online", abs_start_hour=s0)
            df = strat.run(ls, ps,
                          lambda cur, tot, msg: setattr(task, 'progress', min(99, 10 + int(cur/tot*89))) or db.session.commit(),
                          threading.Event(), threading.Event())
        else:
            raise ValueError(f"未知策略: {strategy}")
        
        if df is None:
            raise ValueError("计算被终止")
        
        task.progress = 95
        db.session.commit()
        
        # 经济评估
        ec = econ(df, p)
        
        # 保存结果 + 生成所有图表
        result_dir = user_dir / f's1_{strategy}_{_dt.datetime.now().strftime("%Y%m%d_%H%M%S")}'
        result_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(result_dir / '全年逐时结果.csv', index=False, encoding='utf-8-sig')
        
        # 经济指标
        with open(result_dir / '经济指标.txt', 'w', encoding='utf-8') as fw:
            fw.write(f"策略: {strategy}\n")
            fw.write(f"首年综合盈利: {ec['first_year_profit']:,.2f} 元\n")
            fw.write(f"电价收益: {ec.get('electricity_profit', 0):,.2f} 元\n")
            fw.write(f"容量节省: {ec.get('capacity_saving', 0):,.2f} 元\n")
            fw.write(f"回收期: {ec['payback']:.2f} 年\n")
            fw.write(f"生命周期净利润: {ec.get('lifecycle_profit', 0):,.2f} 元\n")
            if 'life_years' in ec:
                fw.write(f"电池循环寿命: {ec['life_years']:.2f} 年\n")
        
        # 生成图表（使用原始Plt绘图类）
        Plt = _ENGINE_GLOBALS['Plt']
        cp = p['capacity_price']
        cap_kw = p['battery_capacity_mw'] * 1000
        smin = p.get('soc_min', 0.1)
        smax = p.get('soc_max', 0.95)
        
        try:
            Plt.monthly_bar(df, result_dir, cp)
            Plt.peak_cmp(df, result_dir)
            if strategy == 'MILP':
                Plt.lifecycle(ec, result_dir)
            else:
                Plt.scatter(df, result_dir)
                for m, d in [(1,15), (7,15)]:
                    try: Plt.fcast_vs_real(df, m, d, result_dir, 
                        df.attrs.get('Load_R2'), df.attrs.get('Load_RMSE'), df.attrs.get('Load_MAPE'))
                    except: pass
            if strategy != 'MILP':
                for m, d in [(1,15), (7,15)]:
                    try: Plt.typical(df, m, d, result_dir, cap_kw, smin, smax)
                    except: pass
        except Exception as _e:
            print(f"Chart generation warning: {_e}")
        
        return str(result_dir)
    except Exception as e:
        traceback.print_exc()
        raise e

def _run_s2_compute(task, params):
    """电-荷-储协同计算 — 使用原始Step2Strat引擎"""
    try:
        _reload_engine()  # 每次计算前重新加载最新代码
        import numpy as np
        import pandas as pd
        import datetime as _dt
        
        U = _ENGINE_GLOBALS['U']
        C = _ENGINE_GLOBALS['C']
        Step2Strat = _ENGINE_GLOBALS['Step2Strat']
        
        user = User.query.get(task.user_id)
        user_dir = USER_DATA_ROOT / user.phone if user else USER_DATA_ROOT / 'default'
        user_dir.mkdir(parents=True, exist_ok=True)
        
        # 读取参数
        p = C.DP.copy()
        for k in ['battery_capacity_mw','unit_investment','annual_om_cost','capacity_price',
                  'charge_efficiency','discharge_efficiency','dod','cycle_life','max_c_rate']:
            if k in params:
                p[k] = float(params[k])
        for k in ['pv_capacity_mw','wind_capacity_mw','grid_avg_price']:
            if k in params:
                p[k] = float(params[k])
        p['soc_min'] = max(p.get('soc_min', 0.1), 0.05)
        p['soc_max'] = min(0.95, p.get('soc_min', 0.1) + p.get('dod', 0.85))
        
        # 加载负荷
        load_data = None
        load_files = sorted(user_dir.glob('load_*.xlsx'), key=lambda x: x.stat().st_mtime, reverse=True)
        if load_files:
            df = pd.read_excel(load_files[0])
            if 'Load' in df.columns:
                load_data = df['Load'].values
            elif len(df.columns) >= 2:
                load_data = df.iloc[:, 1].values
        
        if load_data is None:
            load_df = U.gen_load()
            load = load_df['Load'].values[:8760]
        else:
            load = np.abs(load_data)[:8760]
        
        prices = U.build_price(C.PG)[:8760]
        
        # 天气和市场数据（可选）
        wdf = None
        weather_files = sorted(user_dir.glob('weather_*.xlsx'), key=lambda x: x.stat().st_mtime, reverse=True)
        if weather_files:
            wdf = pd.read_excel(weather_files[0])
        
        mdf = None
        price_files = sorted(user_dir.glob('price_*.xlsx'), key=lambda x: x.stat().st_mtime, reverse=True)
        if price_files:
            mdf = pd.read_excel(price_files[0])
        
        hh = int(params.get('train_hours', 336))
        fi = int(params.get('ft_interval', 2))
        sh = int(params.get('sim_hours', 8760))
        s0 = int(params.get('start_hour', 0))
        
        strat = Step2Strat(p, wdf, mdf, hh, fi)
        ls = load[s0:s0+sh]
        ps = prices[s0:s0+sh]
        
        task.progress = 10
        db.session.commit()
        
        df = strat.run(ls, ps,
                      lambda cur, tot, msg: setattr(task, 'progress', min(99, 10 + int(cur/tot*89))) or db.session.commit(),
                      threading.Event(), threading.Event())
        
        if df is None:
            raise ValueError("计算被终止")
        
        task.progress = 95
        db.session.commit()
        
        # 保存结果 + 图表
        result_dir = user_dir / f's2_{_dt.datetime.now().strftime("%Y%m%d_%H%M%S")}'
        result_dir.mkdir(parents=True, exist_ok=True)
        df.to_csv(result_dir / '全年逐时结果.csv', index=False, encoding='utf-8-sig')
        
        with open(result_dir / '经济指标.txt', 'w', encoding='utf-8') as fw:
            fw.write(f"=== 电-荷-储协同计算结果 ===\n")
            fw.write(f"负荷预测 R²: {df.attrs.get('Load_R2', 0):.4f}\n")
            fw.write(f"电价预测 R²: {df.attrs.get('Price_R2', 0):.4f}\n")
        
        # 生成图表
        Plt = _ENGINE_GLOBALS['Plt']
        cp = p.get('capacity_price', 30)
        try:
            Plt.monthly_bar(df, result_dir, cp)
            Plt.peak_cmp(df, result_dir)
            Plt.purchase_cost(df, result_dir)
            Plt.scatter(df, result_dir)
            for m, d in [(1,15), (7,15)]:
                try: Plt.fcast_vs_real(df, m, d, result_dir)
                except: pass
                try: Plt.price_fcast_vs_real(df, m, d, result_dir)
                except: pass
        except Exception as _e:
            print(f"S2 chart warning: {_e}")
        
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
# API: 展示结果图表
# ============================================================
@app.route('/api/task/<int:task_id>/charts')
@login_required
def api_task_charts(task_id):
    task = ComputeTask.query.get(task_id)
    if not task or task.user_id != current_user.id:
        return jsonify({'error': '任务不存在'}), 404
    if task.status != 'completed' or not task.result_file:
        return jsonify({'error': '结果不可用'}), 400
    
    result_dir = Path(task.result_file)
    if not result_dir.exists():
        return jsonify({'error': '结果文件丢失'}), 404
    
    charts = []
    for f in sorted(result_dir.iterdir()):
        if f.suffix.lower() in ('.png', '.jpg', '.jpeg'):
            charts.append({'name': f.name, 'url': url_for('api_task_chart_file', task_id=task_id, filename=f.name)})
    return jsonify({'charts': charts})

@app.route('/api/task/<int:task_id>/charts/<filename>')
@login_required
def api_task_chart_file(task_id, filename):
    task = ComputeTask.query.get(task_id)
    if not task or task.user_id != current_user.id:
        return jsonify({'error': '任务不存在'}), 404
    result_dir = Path(task.result_file)
    fp = result_dir / filename
    if not fp.exists():
        return jsonify({'error': '文件不存在'}), 404
    return send_file(str(fp), mimetype='image/png')

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
