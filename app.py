import os
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta
from functools import wraps
from zoneinfo import ZoneInfo
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fogospiromax-dev-secret')

SAO_PAULO = ZoneInfo('America/Sao_Paulo')

def now_sp():
    """Retorna o datetime atual no fuso de São Paulo."""
    return datetime.now(SAO_PAULO)

def today_sp():
    """Retorna a data de hoje em São Paulo como string ISO (YYYY-MM-DD)."""
    return now_sp().date().isoformat()

def now_sp_str():
    """Retorna datetime atual formatado como string legível."""
    return now_sp().strftime('%d/%m/%Y %H:%M')

def get_db():
    database_url = os.environ.get('DATABASE_URL', '')
    # Render usa "postgres://" mas psycopg2 precisa de "postgresql://"
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(database_url)

def send_email_notificacao(req):
    """Envia e-mail de notificação para flavia@piromax.com.br quando uma nova
    solicitação é inserida. Falhas silenciosas — nunca interrompem o fluxo."""
    smtp_host = os.environ.get('SMTP_HOST', '')
    smtp_port = int(os.environ.get('SMTP_PORT', 587))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_pass = os.environ.get('SMTP_PASSWORD', '')

    if not all([smtp_host, smtp_user, smtp_pass]):
        print("[email] SMTP não configurado — notificação ignorada.")
        return

    urgente_txt = "🔴 URGENTE" if req.get('urgente') else "Normal"
    tipo_map = {
        'manutencao': 'Manutenção',
        'compras': 'Compras',
        'rh': 'RH',
        'ti': 'TI',
        'outro': 'Outro',
    }
    tipo_legivel = tipo_map.get(req.get('tipo', 'outro'), req.get('tipo', 'Outro').capitalize())

    subject = f"[Piromax] Nova Solicitação — {tipo_legivel} ({urgente_txt})"

    body = f"""Nova solicitação registrada no sistema Fogos Piromax.

Tipo:       {tipo_legivel}
Prioridade: {urgente_txt}
Data/hora:  {req.get('created_at', '')}

Descrição:
{req.get('descricao', '')}

---
Acesse o painel do gestor para responder: /admin/requests
"""

    msg = MIMEMultipart()
    msg['From'] = smtp_user
    msg['To'] = 'flavia@piromax.com.br'
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, ['flavia@piromax.com.br'], msg.as_string())
        print(f"[email] Notificação enviada para flavia@piromax.com.br (solicitação {req.get('id', '')})")
    except Exception as e:
        print(f"[email] Falha ao enviar notificação: {e}")


def group_by_cliente(orders):
    """Agrupa lista de pedidos por cliente (já deve estar ordenada por cliente)."""
    from itertools import groupby
    result = []
    for cliente, grp in groupby(orders, key=lambda o: o['cliente']):
        result.append({'cliente': cliente, 'produtos': list(grp)})
    return result

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            date TEXT NOT NULL,
            produto TEXT NOT NULL,
            quantidade TEXT NOT NULL,
            concluido BOOLEAN DEFAULT FALSE,
            assinatura TEXT DEFAULT '',
            concluido_em TEXT DEFAULT '',
            urgente BOOLEAN DEFAULT FALSE
        )
    ''')
    cur.execute('''
        ALTER TABLE tasks ADD COLUMN IF NOT EXISTS urgente BOOLEAN DEFAULT FALSE
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            tipo TEXT NOT NULL,
            descricao TEXT NOT NULL,
            urgente BOOLEAN DEFAULT FALSE,
            status TEXT DEFAULT 'pendente',
            resposta TEXT DEFAULT '',
            respondido_em TEXT DEFAULT ''
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS production_schedule (
            id TEXT PRIMARY KEY,
            employee_name TEXT NOT NULL,
            week_start DATE NOT NULL,
            mon TEXT DEFAULT '',
            tue TEXT DEFAULT '',
            wed TEXT DEFAULT '',
            thu TEXT DEFAULT '',
            fri TEXT DEFAULT '',
            CONSTRAINT uq_emp_week UNIQUE (employee_name, week_start)
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS special_orders (
            id TEXT PRIMARY KEY,
            produto TEXT NOT NULL,
            cliente TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            urgente BOOLEAN DEFAULT FALSE,
            concluido BOOLEAN DEFAULT FALSE,
            concluido_por TEXT DEFAULT '',
            concluido_em TEXT DEFAULT '',
            criado_em TEXT NOT NULL,
            data_entrega TEXT DEFAULT '',
            quantidade_produzida INTEGER DEFAULT 0
        )
    ''')
    # Migrações para bancos já existentes
    cur.execute("ALTER TABLE special_orders ADD COLUMN IF NOT EXISTS data_entrega TEXT DEFAULT ''")
    cur.execute("ALTER TABLE special_orders ADD COLUMN IF NOT EXISTS quantidade_produzida INTEGER DEFAULT 0")
    conn.commit()
    cur.close()
    conn.close()

# Cria / migra tabelas na primeira vez que o app sobe
try:
    init_db()
except Exception as e:
    print(f"[init_db] Aviso: {e}")

def next_monday_date():
    """Retorna a próxima segunda-feira (SP). Se hoje já for segunda, vai para a seguinte."""
    today = now_sp().date()
    days_until = (7 - today.weekday()) % 7  # weekday() 0=Seg
    if days_until == 0:
        days_until = 7
    return today + timedelta(days=days_until)

def get_tasks(date_str):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM tasks WHERE date = %s ORDER BY id', (date_str,))
    tasks = [dict(t) for t in cur.fetchall()]
    cur.close()
    conn.close()
    return tasks

# ── Login ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
        admin_password = os.environ.get('ADMIN_PASSWORD', 'fogos2025')
        if username == admin_username and password == admin_password:
            session['admin_logged_in'] = True
            return redirect(url_for('admin_view'))
        else:
            error = 'Usuário ou senha incorretos. Tente novamente.'
    return render_template('login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin_login'))

# ── Trabalhador — Hub ──────────────────────────────────────────────────────────
@app.route('/')
def worker_hub():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) as cnt FROM special_orders WHERE concluido = FALSE")
        pedidos_row = cur.fetchone()
        cur.execute("SELECT COUNT(*) as cnt FROM requests WHERE status = 'pendente'")
        solicitacoes_row = cur.fetchone()
        cur.close()
        conn.close()
        pedidos_pendentes_count = pedidos_row['cnt'] if pedidos_row else 0
        solicitacoes_pendentes_count = solicitacoes_row['cnt'] if solicitacoes_row else 0
    except Exception:
        pedidos_pendentes_count = 0
        solicitacoes_pendentes_count = 0
    return render_template('worker_hub.html',
                           pedidos_pendentes_count=pedidos_pendentes_count,
                           solicitacoes_pendentes_count=solicitacoes_pendentes_count)

# ── Trabalhador — Produção por Semana ─────────────────────────────────────────
@app.route('/producao')
def worker_view():
    default_week = next_monday_date().isoformat()
    return render_template('worker_producao.html', default_week=default_week)

@app.route('/worker/update', methods=['POST'])
def worker_update():
    data = request.json
    concluido_em = ''
    if data.get('concluido'):
        concluido_em = now_sp().strftime('%H:%M')
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'UPDATE tasks SET concluido=%s, assinatura=%s, concluido_em=%s '
        'WHERE id=%s AND date=%s',
        (data['concluido'], data.get('assinatura', ''), concluido_em,
         data['id'], data['date'])
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'concluido_em': concluido_em})

# ── Trabalhador — Solicitações ─────────────────────────────────────────────────
@app.route('/requests')
def requests_view():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM requests ORDER BY created_at DESC")
    reqs = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    pendentes = [r for r in reqs if r['status'] == 'pendente']
    concluidas = [r for r in reqs if r['status'] == 'concluida']
    return render_template('requests.html', pendentes=pendentes, concluidas=concluidas)

@app.route('/requests/add', methods=['POST'])
def requests_add():
    data = request.json
    req = {
        'id': str(uuid.uuid4()),
        'created_at': now_sp_str(),
        'tipo': data.get('tipo', 'outro'),
        'descricao': data.get('descricao', '').strip(),
        'urgente': bool(data.get('urgente', False)),
        'status': 'pendente',
        'resposta': '',
        'respondido_em': ''
    }
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO requests (id, created_at, tipo, descricao, urgente, status, resposta, respondido_em) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
        (req['id'], req['created_at'], req['tipo'], req['descricao'],
         req['urgente'], req['status'], req['resposta'], req['respondido_em'])
    )
    conn.commit()
    cur.close()
    conn.close()

    # Notificação por e-mail (erro silencioso — não interrompe a resposta)
    try:
        send_email_notificacao(req)
    except Exception as e:
        print(f"[email] Erro inesperado na notificação: {e}")

    return jsonify({'success': True, 'request': req})

@app.route('/requests/<req_id>/editar', methods=['POST'])
def worker_requests_editar(req_id):
    """Permite que o trabalhador edite uma solicitação ainda pendente."""
    data = request.json
    tipo      = data.get('tipo', 'outro')
    descricao = (data.get('descricao') or '').strip()
    urgente   = bool(data.get('urgente', False))
    if not descricao:
        return jsonify({'success': False, 'error': 'Descrição vazia'}), 400
    conn = get_db()
    cur = conn.cursor()
    # Só edita se ainda estiver pendente
    cur.execute(
        "UPDATE requests SET tipo=%s, descricao=%s, urgente=%s WHERE id=%s AND status='pendente'",
        (tipo, descricao, urgente, req_id)
    )
    updated = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if updated == 0:
        return jsonify({'success': False, 'error': 'Não encontrado ou já concluído'}), 404
    return jsonify({'success': True})

# ── Trabalhador — Pedidos Especiais ───────────────────────────────────────────
@app.route('/pedidos')
def worker_pedidos_view():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM special_orders ORDER BY cliente, criado_em")
    orders = [dict(o) for o in cur.fetchall()]
    cur.close()
    conn.close()

    pendentes = [o for o in orders if not o['concluido']]
    concluidos = sorted([o for o in orders if o['concluido']],
                        key=lambda o: o['concluido_em'], reverse=True)

    urgentes = sorted([o for o in pendentes if o['urgente']],
                      key=lambda o: o['cliente'].lower())
    normais  = sorted([o for o in pendentes if not o['urgente']],
                      key=lambda o: o['cliente'].lower())

    return render_template('worker_pedidos.html',
                           urgentes_clientes=group_by_cliente(urgentes),
                           normais_clientes=group_by_cliente(normais),
                           concluidos=concluidos,
                           total_pendentes=len(pendentes),
                           total_concluidos=len(concluidos))

@app.route('/pedidos/<order_id>/concluir', methods=['POST'])
def worker_pedidos_concluir(order_id):
    data = request.json
    nome = data.get('nome', '').strip() or 'Trabalhador'
    produzida_agora = int(data.get('quantidade_produzida', 0))

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM special_orders WHERE id=%s', (order_id,))
    order = cur.fetchone()
    cur.close()

    if not order:
        conn.close()
        return jsonify({'success': False, 'error': 'Pedido não encontrado'}), 404

    total_produzida = (order['quantidade_produzida'] or 0) + produzida_agora
    saldo_restante  = order['quantidade'] - total_produzida
    concluido_em    = now_sp_str()
    fully_done      = saldo_restante <= 0

    cur2 = conn.cursor()
    if fully_done:
        cur2.execute(
            'UPDATE special_orders SET concluido=%s, concluido_por=%s, concluido_em=%s, '
            'quantidade_produzida=%s WHERE id=%s',
            (True, nome, concluido_em, total_produzida, order_id)
        )
    else:
        cur2.execute(
            'UPDATE special_orders SET quantidade_produzida=%s, concluido_por=%s, '
            'concluido_em=%s WHERE id=%s',
            (total_produzida, nome, concluido_em, order_id)
        )
    conn.commit()
    cur2.close()
    conn.close()
    return jsonify({
        'success': True,
        'concluido': fully_done,
        'saldo_restante': max(0, saldo_restante),
        'concluido_em': concluido_em,
        'concluido_por': nome
    })

# ── Gestor / Admin — Hub ───────────────────────────────────────────────────────
@app.route('/admin')
@login_required
def admin_view():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT COUNT(*) as cnt FROM requests WHERE status = 'pendente'")
    row = cur.fetchone()
    pedidos_row = None
    try:
        cur.execute("SELECT COUNT(*) as cnt FROM special_orders WHERE concluido = FALSE")
        pedidos_row = cur.fetchone()
    except Exception:
        pass
    cur.close()
    conn.close()
    pendentes_count = row['cnt'] if row else 0
    pedidos_pendentes_count = pedidos_row['cnt'] if pedidos_row else 0
    return render_template('admin_hub.html',
                           pendentes_count=pendentes_count,
                           pedidos_pendentes_count=pedidos_pendentes_count)

# ── Gestor / Admin — Produção por Semana ──────────────────────────────────────
@app.route('/admin/producao')
@login_required
def admin_producao_view():
    default_week = next_monday_date().isoformat()
    return render_template('admin_producao.html', default_week=default_week)

# ── API — Produção: buscar semana ─────────────────────────────────────────────
@app.route('/api/producao-semana')
def api_producao_semana():
    week_start = request.args.get('week_start', '')
    if not week_start:
        week_start = next_monday_date().isoformat()
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        'SELECT * FROM production_schedule WHERE week_start=%s ORDER BY employee_name',
        (week_start,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    for r in rows:
        if hasattr(r.get('week_start'), 'isoformat'):
            r['week_start'] = r['week_start'].isoformat()
    return jsonify(rows)

# ── Admin — Produção: salvar semana ───────────────────────────────────────────
@app.route('/admin/producao/salvar', methods=['POST'])
@login_required
def admin_producao_salvar():
    data = request.json
    week_start = data.get('week_start', '')
    employees  = data.get('employees', [])
    if not week_start or not employees:
        return jsonify({'success': False, 'error': 'Dados incompletos'}), 400
    conn = get_db()
    cur = conn.cursor()
    for emp in employees:
        name = (emp.get('name') or '').strip()
        if not name:
            continue
        mon = emp.get('mon', '') or ''
        tue = emp.get('tue', '') or ''
        wed = emp.get('wed', '') or ''
        thu = emp.get('thu', '') or ''
        fri = emp.get('fri', '') or ''
        cur.execute('''
            INSERT INTO production_schedule (id, employee_name, week_start, mon, tue, wed, thu, fri)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (employee_name, week_start)
            DO UPDATE SET mon=%s, tue=%s, wed=%s, thu=%s, fri=%s
        ''', (str(uuid.uuid4()), name, week_start, mon, tue, wed, thu, fri,
              mon, tue, wed, thu, fri))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'saved': len(employees)})

# ── Admin — Produção: remover funcionário da semana ──────────────────────────
@app.route('/admin/producao/remover', methods=['POST'])
@login_required
def admin_producao_remover():
    data = request.json
    name       = data.get('name', '').strip()
    week_start = data.get('week_start', '')
    if not name or not week_start:
        return jsonify({'success': False}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'DELETE FROM production_schedule WHERE employee_name=%s AND week_start=%s',
        (name, week_start)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/add', methods=['POST'])
@login_required
def admin_add():
    data = request.json
    task = {
        'id': str(uuid.uuid4()),
        'date': data['date'],
        'produto': data['produto'].strip(),
        'quantidade': str(data['quantidade']),
        'concluido': False,
        'assinatura': '',
        'concluido_em': '',
        'urgente': bool(data.get('urgente', False))
    }
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO tasks (id, date, produto, quantidade, concluido, assinatura, concluido_em, urgente) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
        (task['id'], task['date'], task['produto'], task['quantidade'],
         task['concluido'], task['assinatura'], task['concluido_em'], task['urgente'])
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'task': task})

@app.route('/admin/delete/<task_id>', methods=['DELETE'])
@login_required
def admin_delete(task_id):
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM tasks WHERE id=%s AND date=%s', (task_id, data['date']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/clear', methods=['POST'])
@login_required
def admin_clear():
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM tasks WHERE date=%s', (data['date'],))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/copy-yesterday', methods=['POST'])
@login_required
def admin_copy_yesterday():
    data = request.json
    today_str = data['date']
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT DISTINCT date FROM tasks WHERE date < %s ORDER BY date DESC LIMIT 1",
        (today_str,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return jsonify({'success': False, 'message': 'Nenhum dia anterior com tarefas encontrado.'})
    last_date = row['date']
    last_tasks = get_tasks(last_date)
    if not last_tasks:
        return jsonify({'success': False, 'message': 'Nenhuma tarefa encontrada no último dia.'})
    conn = get_db()
    cur = conn.cursor()
    for t in last_tasks:
        cur.execute(
            'INSERT INTO tasks (id, date, produto, quantidade, concluido, assinatura, concluido_em, urgente) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
            (str(uuid.uuid4()), today_str, t['produto'], t['quantidade'],
             False, '', '', t.get('urgente', False))
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'copied_from': last_date})

# ── Gestor / Admin — Solicitações ─────────────────────────────────────────────
@app.route('/admin/requests')
@login_required
def admin_requests_view():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM requests ORDER BY created_at DESC")
    reqs = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    pendentes = [r for r in reqs if r['status'] == 'pendente']
    concluidas = [r for r in reqs if r['status'] == 'concluida']
    return render_template('admin_requests.html', pendentes=pendentes, concluidas=concluidas)

@app.route('/admin/requests/update', methods=['POST'])
@login_required
def admin_requests_update():
    data = request.json
    req_id = data['id']
    status = data.get('status', 'concluida')
    resp = data.get('resposta', '').strip()
    resp_em = now_sp_str() if status == 'concluida' else ''
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'UPDATE requests SET status=%s, resposta=%s, respondido_em=%s WHERE id=%s',
        (status, resp, resp_em, req_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/requests/delete/<req_id>', methods=['DELETE'])
@login_required
def admin_requests_delete(req_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM requests WHERE id=%s', (req_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/requests/<req_id>/editar', methods=['POST'])
@login_required
def admin_requests_editar(req_id):
    data = request.json
    tipo     = data.get('tipo', 'outro')
    descricao = (data.get('descricao') or '').strip()
    urgente  = bool(data.get('urgente', False))
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'UPDATE requests SET tipo=%s, descricao=%s, urgente=%s WHERE id=%s',
        (tipo, descricao, urgente, req_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# ── Gestor / Admin — Pedidos Especiais ────────────────────────────────────────
@app.route('/admin/pedidos')
@login_required
def admin_pedidos_view():
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM special_orders ORDER BY cliente, criado_em")
    orders = [dict(o) for o in cur.fetchall()]
    cur.close()
    conn.close()

    pendentes = [o for o in orders if not o['concluido']]
    concluidos = sorted([o for o in orders if o['concluido']],
                        key=lambda o: o['concluido_em'], reverse=True)

    urgentes = sorted([o for o in pendentes if o['urgente']],
                      key=lambda o: o['cliente'].lower())
    normais  = sorted([o for o in pendentes if not o['urgente']],
                      key=lambda o: o['cliente'].lower())

    return render_template('admin_pedidos.html',
                           urgentes_clientes=group_by_cliente(urgentes),
                           normais_clientes=group_by_cliente(normais),
                           concluidos=concluidos,
                           total_pendentes=len(pendentes),
                           total_concluidos=len(concluidos))

@app.route('/admin/pedidos/add', methods=['POST'])
@login_required
def admin_pedidos_add():
    data = request.json
    cliente = data.get('cliente', '').strip()
    produtos = data.get('produtos', [])

    if not cliente or not produtos:
        return jsonify({'success': False, 'error': 'Dados incompletos'}), 400

    conn = get_db()
    cur = conn.cursor()
    criado_em = now_sp_str()
    for p in produtos:
        cur.execute(
            'INSERT INTO special_orders '
            '(id, produto, cliente, quantidade, urgente, concluido, concluido_por, '
            'concluido_em, criado_em, data_entrega, quantidade_produzida) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
            (str(uuid.uuid4()), p['produto'].strip(), cliente,
             int(p['quantidade']), bool(p.get('urgente', False)),
             False, '', '', criado_em,
             p.get('data_entrega', '').strip(), 0)
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'count': len(produtos)})

@app.route('/admin/pedidos/<order_id>/editar', methods=['POST'])
@login_required
def admin_pedidos_editar(order_id):
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'UPDATE special_orders SET produto=%s, quantidade=%s, data_entrega=%s, urgente=%s WHERE id=%s',
        (data['produto'].strip(), int(data['quantidade']),
         data.get('data_entrega', '').strip(),
         bool(data.get('urgente', False)), order_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/pedidos/<order_id>/concluir', methods=['POST'])
@login_required
def admin_pedidos_concluir(order_id):
    data = request.json or {}
    nome = data.get('nome', 'Gestor').strip() or 'Gestor'
    produzida_agora = int(data.get('quantidade_produzida', 0))

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute('SELECT * FROM special_orders WHERE id=%s', (order_id,))
    order = cur.fetchone()
    cur.close()

    if not order:
        conn.close()
        return jsonify({'success': False}), 404

    total_produzida = (order['quantidade_produzida'] or 0) + produzida_agora
    saldo_restante  = order['quantidade'] - total_produzida
    concluido_em    = now_sp_str()
    fully_done      = saldo_restante <= 0

    cur2 = conn.cursor()
    if fully_done:
        cur2.execute(
            'UPDATE special_orders SET concluido=%s, concluido_por=%s, concluido_em=%s, '
            'quantidade_produzida=%s WHERE id=%s',
            (True, nome, concluido_em, total_produzida, order_id)
        )
    else:
        cur2.execute(
            'UPDATE special_orders SET quantidade_produzida=%s, concluido_por=%s, '
            'concluido_em=%s WHERE id=%s',
            (total_produzida, nome, concluido_em, order_id)
        )
    conn.commit()
    cur2.close()
    conn.close()
    return jsonify({
        'success': True,
        'concluido': fully_done,
        'saldo_restante': max(0, saldo_restante),
        'concluido_em': concluido_em
    })

@app.route('/admin/pedidos/<order_id>/reabrir', methods=['POST'])
@login_required
def admin_pedidos_reabrir(order_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'UPDATE special_orders SET concluido=%s, concluido_por=%s, '
        'concluido_em=%s, quantidade_produzida=%s WHERE id=%s',
        (False, '', '', 0, order_id)
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/pedidos/<order_id>/excluir', methods=['DELETE'])
@login_required
def admin_pedidos_excluir(order_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM special_orders WHERE id=%s', (order_id,))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})

# ── API ────────────────────────────────────────────────────────────────────────
@app.route('/api/tasks')
def api_tasks():
    date_str = request.args.get('date', today_sp())
    return jsonify(get_tasks(date_str))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
