from flask import Flask, render_template, request, jsonify
import json
import os
from datetime import date, datetime
import uuid

app = Flask(__name__)

DATA_FILE = 'tasks.json'


def load_tasks():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_tasks(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ─── Tela do Trabalhador ────────────────────────────────────────────────────

@app.route('/')
def worker():
    today = str(date.today())
    tasks = load_tasks()
    day_tasks = tasks.get(today, [])
    total = len(day_tasks)
    concluidos = sum(1 for t in day_tasks if t.get('concluido'))
    return render_template('worker.html', tasks=day_tasks, date=today,
                           total=total, concluidos=concluidos)


@app.route('/worker/update', methods=['POST'])
def update_task():
    data = request.get_json()
    task_date = data.get('date', str(date.today()))
    task_id = data['id']
    tasks = load_tasks()
    if task_date in tasks:
        for task in tasks[task_date]:
            if task['id'] == task_id:
                task['concluido'] = data.get('concluido', task['concluido'])
                task['assinatura'] = data.get('assinatura', task['assinatura'])
                if task['concluido'] and not task.get('concluido_em'):
                    task['concluido_em'] = datetime.now().strftime('%H:%M')
                elif not task['concluido']:
                    task['concluido_em'] = None
                break
        save_tasks(tasks)
    return jsonify({'success': True})


# ─── Tela do Gestor (Admin) ─────────────────────────────────────────────────

@app.route('/admin')
def admin():
    today = str(date.today())
    selected_date = request.args.get('date', today)
    tasks = load_tasks()
    day_tasks = tasks.get(selected_date, [])
    return render_template('admin.html', tasks=day_tasks,
                           date=selected_date, today=today)


@app.route('/admin/add', methods=['POST'])
def add_task():
    data = request.get_json()
    task_date = data.get('date', str(date.today()))
    tasks = load_tasks()
    if task_date not in tasks:
        tasks[task_date] = []
    new_task = {
        'id': str(uuid.uuid4()),
        'produto': data['produto'].strip(),
        'quantidade': int(data['quantidade']),
        'concluido': False,
        'assinatura': '',
        'concluido_em': None
    }
    tasks[task_date].append(new_task)
    save_tasks(tasks)
    return jsonify({'success': True, 'task': new_task})


@app.route('/admin/delete/<task_id>', methods=['DELETE'])
def delete_task(task_id):
    data = request.get_json()
    task_date = data.get('date', str(date.today()))
    tasks = load_tasks()
    if task_date in tasks:
        tasks[task_date] = [t for t in tasks[task_date] if t['id'] != task_id]
        save_tasks(tasks)
    return jsonify({'success': True})


@app.route('/admin/clear', methods=['POST'])
def clear_tasks():
    data = request.get_json()
    task_date = data.get('date', str(date.today()))
    tasks = load_tasks()
    tasks[task_date] = []
    save_tasks(tasks)
    return jsonify({'success': True})


@app.route('/admin/copy-yesterday', methods=['POST'])
def copy_yesterday():
    from datetime import timedelta
    data = request.get_json()
    target_date = data.get('date', str(date.today()))
    target_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
    yesterday = str(target_dt - timedelta(days=1))
    tasks = load_tasks()
    if yesterday in tasks:
        new_tasks = []
        for t in tasks[yesterday]:
            new_tasks.append({
                'id': str(uuid.uuid4()),
                'produto': t['produto'],
                'quantidade': t['quantidade'],
                'concluido': False,
                'assinatura': '',
                'concluido_em': None
            })
        tasks[target_date] = new_tasks
        save_tasks(tasks)
        return jsonify({'success': True, 'tasks': new_tasks})
    return jsonify({'success': False, 'message': 'Nenhuma tarefa ontem.'})


# ─── API: buscar tarefas de uma data ─────────────────────────────────────────

@app.route('/api/tasks')
def api_tasks():
    task_date = request.args.get('date', str(date.today()))
    tasks = load_tasks()
    return jsonify(tasks.get(task_date, []))


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
