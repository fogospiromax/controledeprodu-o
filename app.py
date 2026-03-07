import os
import uuid
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


def get_db():
    database_url = os.environ.get('DATABASE_URL', '')
    # Render usa "postgres://" mas psycopg2 precisa de "postgresql://"
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    return psycopg2.connect(database_url)


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id           TEXT PRIMARY KEY,
            date         TEXT NOT NULL,
            produto      TEXT NOT NULL,
            quantidade   TEXT NOT NULL,
            concluido    BOOLEAN DEFAULT FALSE,
            assinatura   TEXT DEFAULT '',
            concluido_em TEXT DEFAULT '',
            urgente      BOOLEAN DEFAULT FALSE
        )
    ''')
    # Garante coluna urgente mesmo em tabelas criadas antes desta versão
    cur.execute('''
        ALTER TABLE tasks ADD COLUMN IF NOT EXISTS urgente BOOLEAN DEFAULT FALSE
    ''')
    conn.commit()
    cur.close()
    conn.close()


# Cria / migra a tabela na primeira vez que o app sobe
try:
    init_db()
except Exception as e:
    print(f"[init_db] Aviso: {e}")


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


# ── Trabalhador ────────────────────────────────────────────────────────────────

@app.route('/')
def worker_view():
    today    = today_sp()
    date_str = request.args.get('date', today)

    tasks      = get_tasks(date_str)
    total      = len(tasks)
    concluidos = sum(1 for t in tasks if t['concluido'])
    return render_template('worker.html',
                           tasks=tasks,
                           date=date_str,
                           today=today,
                           total=total,
                           concluidos=concluidos)


@app.route('/worker/update', methods=['POST'])
def worker_update():
    data        = request.json
    concluido_em = ''
    if data.get('concluido'):
        concluido_em = now_sp().strftime('%H:%M')
    conn = get_db()
    cur  = conn.cursor()
    cur.execute(
        'UPDATE tasks SET concluido=%s, assinatura=%s, concluido_em=%s '
        'WHERE id=%s AND date=%s',
        (data['concluido'], data.get('assinatura', ''),
         concluido_em, data['id'], data['date'])
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'concluido_em': concluido_em})


# ── Gestor / Admin ─────────────────────────────────────────────────────────────

@app.route('/admin')
@login_required
def admin_view():
    date_str = request.args.get('date', today_sp())
    tasks    = get_tasks(date_str)
    return render_template('admin.html', tasks=tasks, date=date_str, today=today_sp())


@app.route('/admin/add', methods=['POST'])
@login_required
def admin_add():
    data = request.json
    task = {
        'id':           str(uuid.uuid4()),
        'date':         data['date'],
        'produto':      data['produto'].strip(),
        'quantidade':   str(data['quantidade']),
        'concluido':    False,
        'assinatura':   '',
        'concluido_em': '',
        'urgente':      bool(data.get('urgente', False))
    }
    conn = get_db()
    cur  = conn.cursor()
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
    cur  = conn.cursor()
    cur.execute('DELETE FROM tasks WHERE id=%s AND date=%s',
                (task_id, data['date']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/admin/clear', methods=['POST'])
@login_required
def admin_clear():
    data = request.json
    conn = get_db()
    cur  = conn.cursor()
    cur.execute('DELETE FROM tasks WHERE date=%s', (data['date'],))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/admin/copy-yesterday', methods=['POST'])
@login_required
def admin_copy_yesterday():
    data      = request.json
    today_str = data['date']

    # Encontra o último dia com tarefas ANTES de today_str (qualquer dia, não só ontem)
    conn = get_db()
    cur  = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT DISTINCT date FROM tasks WHERE date < %s ORDER BY date DESC LIMIT 1",
        (today_str,)
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return jsonify({'success': False, 'message': 'Nenhum dia anterior com tarefas encontrado.'})

    last_date  = row['date']
    last_tasks = get_tasks(last_date)
    if not last_tasks:
        return jsonify({'success': False, 'message': 'Nenhuma tarefa encontrada no último dia.'})

    conn = get_db()
    cur  = conn.cursor()
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


@app.route('/api/tasks')
def api_tasks():
    date_str = request.args.get('date', today_sp())
    return jsonify(get_tasks(date_str))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
