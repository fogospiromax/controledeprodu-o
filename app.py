import os
import uuid
from datetime import datetime, date, timedelta
from flask import Flask, render_template, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)


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
            concluido_em TEXT DEFAULT ''
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()


# Cria a tabela na primeira vez que o app sobe
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


# ── Trabalhador ────────────────────────────────────────────────────────────────

@app.route('/')
def worker_view():
    today = date.today().isoformat()
    tasks = get_tasks(today)
    total = len(tasks)
    concluidos = sum(1 for t in tasks if t['concluido'])
    return render_template('worker.html', tasks=tasks, date=today,
                           total=total, concluidos=concluidos)


@app.route('/worker/update', methods=['POST'])
def worker_update():
    data = request.json
    concluido_em = ''
    if data.get('concluido'):
        concluido_em = datetime.now().strftime('%H:%M')
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'UPDATE tasks SET concluido=%s, assinatura=%s, concluido_em=%s '
        'WHERE id=%s AND date=%s',
        (data['concluido'], data.get('assinatura', ''),
         concluido_em, data['id'], data['date'])
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


# ── Gestor / Admin ─────────────────────────────────────────────────────────────

@app.route('/admin')
def admin_view():
    date_str = request.args.get('date', date.today().isoformat())
    tasks = get_tasks(date_str)
    return render_template('admin.html', tasks=tasks, date=date_str)


@app.route('/admin/add', methods=['POST'])
def admin_add():
    data = request.json
    task = {
        'id': str(uuid.uuid4()),
        'date': data['date'],
        'produto': data['produto'].strip(),
        'quantidade': str(data['quantidade']),
        'concluido': False,
        'assinatura': '',
        'concluido_em': ''
    }
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO tasks (id, date, produto, quantidade, concluido, assinatura, concluido_em) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s)',
        (task['id'], task['date'], task['produto'], task['quantidade'],
         task['concluido'], task['assinatura'], task['concluido_em'])
    )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True, 'task': task})


@app.route('/admin/delete/<task_id>', methods=['DELETE'])
def admin_delete(task_id):
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    cur.execute('DELETE FROM tasks WHERE id=%s AND date=%s',
                (task_id, data['date']))
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/admin/clear', methods=['POST'])
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
def admin_copy_yesterday():
    data = request.json
    today = data['date']
    yesterday = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    yesterday_tasks = get_tasks(yesterday)
    if not yesterday_tasks:
        return jsonify({'success': False, 'message': 'Nenhuma tarefa encontrada para ontem.'})
    conn = get_db()
    cur = conn.cursor()
    for t in yesterday_tasks:
        cur.execute(
            'INSERT INTO tasks (id, date, produto, quantidade, concluido, assinatura, concluido_em) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s)',
            (str(uuid.uuid4()), today, t['produto'], t['quantidade'], False, '', '')
        )
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({'success': True})


@app.route('/api/tasks')
def api_tasks():
    date_str = request.args.get('date', date.today().isoformat())
    return jsonify(get_tasks(date_str))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
