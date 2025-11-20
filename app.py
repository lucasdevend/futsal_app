# app.py
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, abort
import os
from datetime import datetime, time, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from fpdf import FPDF
import threading
import time as t
import schedule
from functools import wraps

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

# --------------------------
# CONFIGURAÇÕES
# --------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "sua_chave_secreta_local")

# Use a variável de ambiente DATABASE_URL (defina no Render)
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://presencas_db_user:UXcbXRjabfCBBEedDfhLRsB8IiJy7KSh@dpg-d4f5mk0dl3ps73cn0c5g-a.oregon-postgres.render.com/presencas_db")

# --------------------------
# POOL DE CONEXÕES
# --------------------------
# Ajuste minconn/maxconn conforme necessidade
pg_pool = pool.ThreadedConnectionPool(1, 10, dsn=DATABASE_URL)

def get_conn():
    return pg_pool.getconn()

def put_conn(conn):
    pg_pool.putconn(conn)

def close_all_conns():
    pg_pool.closeall()

# --------------------------
# INICIALIZAÇÃO DO BANCO (Postgres)
# --------------------------
def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # Cria tabelas equivalentes (Postgres)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alunos (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        matricula TEXT NOT NULL,
        data_hora TIMESTAMP NOT NULL,
        numero_chamada INTEGER NOT NULL
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admin (
        id SERIAL PRIMARY KEY,
        usuario TEXT NOT NULL UNIQUE,
        senha TEXT NOT NULL
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS alunos_cadastrados (
        id SERIAL PRIMARY KEY,
        nome TEXT NOT NULL,
        cpf4 TEXT NOT NULL,
        numero_chamada INTEGER UNIQUE
    );
    """)
    # Índice único composto para evitar duplicidade por nome+cpf4
    cur.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_aluno_nome_cpf4 ON alunos_cadastrados (nome, cpf4);
    """)

    # Insere/atualiza admin padrão
    cur.execute("SELECT id FROM admin WHERE usuario = %s", ('admin',))
    if not cur.fetchone():
        cur.execute("INSERT INTO admin (usuario, senha) VALUES (%s, %s)", ('admin', generate_password_hash('551469')))
    else:
        cur.execute("UPDATE admin SET senha = %s WHERE usuario = %s", (generate_password_hash('551469'), 'admin'))

    # Remove duplicatas (mantendo o menor id)
    cur.execute("""
    DELETE FROM alunos_cadastrados
    WHERE id NOT IN (
        SELECT MIN(id) FROM alunos_cadastrados GROUP BY nome, cpf4
    )
    """)
    # Inserir exemplos padrao se a tabela estiver vazia
    cur.execute("SELECT COUNT(*) FROM alunos_cadastrados")
    count = cur.fetchone()[0]
    if count == 0:
        cur.executemany("""
            INSERT INTO alunos_cadastrados (nome, numero_chamada, cpf4)
            VALUES (%s, %s, %s)
        """, [
            ('Thiago Silva', 1, '1995'),
            ('Victor Pereira', 2, '5678'),
            ('Isaque Alves', 3, '1379'),
            ('Eduardo Carvalho', 4, '8299')
        ])

    conn.commit()
    cur.close()
    put_conn(conn)

# Inicializa DB uma vez no start (não recreia se já existe no Postgres)
try:
    init_db()
except Exception as e:
    print("[ERRO init_db]:", e)

# --------------------------
# ROTA PRINCIPAL (ALUNO)
# --------------------------
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        numero_chamada = request.form['numero_chamada'].strip()
        cpf4 = request.form['cpf4'].strip()

        # Validação do CPF (últimos 4 dígitos)
        if not cpf4.isdigit() or len(cpf4) != 4:
            flash("O CPF deve conter exatamente 4 dígitos!", "danger")
            return render_template('index.html')

        # Validação do número de chamada
        if not numero_chamada.isdigit() or not (1 <= int(numero_chamada) <= 30):
            flash("Número de chamada inválido! Deve ser entre 1 e 30.", "danger")
            return render_template('index.html')

        numero_int = int(numero_chamada)
        agora = datetime.now()
        horario_inicio = time(13, 0)  # 13:00
        horario_fim = time(15, 15)    # 15:15

        # Validação de dia da semana (sábado=5, domingo=6)
        if agora.weekday() not in [5, 6]:
            flash("Presença só pode ser registrada aos finais de semana!", "danger")
            return render_template('index.html')

        # Validação de horário
        if not (horario_inicio <= agora.time() <= horario_fim):
            flash("Fora do horário permitido! Apenas das 13:00 às 15:15.", "danger")
            return render_template('index.html')

        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT id, nome FROM alunos_cadastrados WHERE numero_chamada = %s AND cpf4 = %s", (numero_int, cpf4))
        aluno_cad = c.fetchone()

        if not aluno_cad:
            flash("Número de chamada ou CPF inválido!", "danger")
            put_conn(conn)
            return render_template('index.html')

        hoje = agora.strftime("%Y-%m-%d")
        c.execute("SELECT id FROM alunos WHERE numero_chamada = %s AND DATE(data_hora) = %s", (numero_int, hoje))
        registro = c.fetchone()

        if registro:
            flash("Você já registrou sua presença hoje!", "danger")
        else:
            c.execute("INSERT INTO alunos (nome, matricula, data_hora, numero_chamada) VALUES (%s, %s, %s, %s)",
                      (aluno_cad[1], cpf4, agora, numero_int))
            conn.commit()
            flash("Registro enviado com sucesso!", "success")

        c.close()
        put_conn(conn)

    return render_template('index.html')

# ======================================
# LOGIN ADMIN
# ======================================
@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        usuario = request.form['usuario']
        senha = request.form['senha']

        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT id, usuario, senha FROM admin WHERE usuario = %s", (usuario,))
        admin = c.fetchone()
        c.close()
        put_conn(conn)

        if admin and check_password_hash(admin[2], senha):
            session['admin'] = usuario
            return redirect(url_for('admin_dashboard'))
        else:
            flash("Usuário ou senha incorretos!", "danger")

    return render_template('admin_login.html')

# ======================================
# DASHBOARD ADMIN
# ======================================
@app.route('/admin')
def admin_dashboard():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, nome, cpf4, numero_chamada FROM alunos_cadastrados ORDER BY numero_chamada ASC")
    alunos = c.fetchall()
    c.close()
    put_conn(conn)

    return render_template('admin_dashboard.html', alunos=alunos)

# ======================================
# CADASTRAR ALUNO
# ======================================
@app.route('/cadastrar_aluno', methods=['GET', 'POST'])
def cadastrar_aluno():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        nome = request.form['nome'].strip()
        numero_chamada = request.form['numero_chamada'].strip()
        cpf4 = request.form['cpf4'].strip()

        if not nome or not numero_chamada or not cpf4:
            flash("Todos os campos são obrigatórios!", "danger")
            return render_template('cadastrar_aluno.html')

        if not numero_chamada.isdigit():
            flash("Número de chamada deve ser numérico!", "danger")
            return render_template('cadastrar_aluno.html')

        if not cpf4.isdigit() or len(cpf4) != 4:
            flash("CPF deve ter 4 dígitos!", "danger")
            return render_template('cadastrar_aluno.html')

        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT id FROM alunos_cadastrados WHERE nome = %s AND cpf4 = %s", (nome, cpf4))
        if c.fetchone():
            flash("Aluno com esse nome e CPF (últimos 4) já cadastrado!", "danger")
            c.close()
            put_conn(conn)
            return render_template('cadastrar_aluno.html')

        c.execute("SELECT id FROM alunos_cadastrados WHERE numero_chamada = %s", (int(numero_chamada),))
        if c.fetchone():
            flash("Número de chamada já cadastrado!", "danger")
            c.close()
            put_conn(conn)
            return render_template('cadastrar_aluno.html')

        try:
            c.execute("INSERT INTO alunos_cadastrados (nome, numero_chamada, cpf4) VALUES (%s, %s, %s)",
                      (nome, int(numero_chamada), cpf4))
            conn.commit()
            flash("Aluno cadastrado com sucesso!", "success")
        except Exception as e:
            conn.rollback()
            flash("Erro: cadastro duplicado detectado ou outro erro. Verifique os dados.", "danger")
            print("[erro cadastrar_aluno]", e)
        finally:
            c.close()
            put_conn(conn)

    return render_template('cadastrar_aluno.html')

# ======================================
# EDITAR ALUNO
# ======================================
@app.route('/editar/<int:id>', methods=['GET', 'POST'])
def editar_aluno(id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_conn()
    cursor = conn.cursor()

    if request.method == 'POST':
        nome = request.form['nome'].strip()
        cpf4 = request.form['matricula'].strip()
        numero_chamada = request.form['numero_chamada'].strip()

        if not nome or not cpf4 or not numero_chamada:
            flash("Todos os campos são obrigatórios!", "danger")
            return redirect(url_for('editar_aluno', id=id))

        if not cpf4.isdigit() or len(cpf4) != 4:
            flash("CPF deve ter 4 dígitos!", "danger")
            return redirect(url_for('editar_aluno', id=id))

        if not numero_chamada.isdigit() or int(numero_chamada) < 1:
            flash("Número de chamada inválido!", "danger")
            return redirect(url_for('editar_aluno', id=id))

        try:
            cursor.execute("UPDATE alunos_cadastrados SET nome=%s, cpf4=%s, numero_chamada=%s WHERE id=%s",
                           (nome, cpf4, int(numero_chamada), id))
            conn.commit()
            flash("Aluno atualizado com sucesso!", "success")
        except Exception as e:
            conn.rollback()
            flash("Erro: nome + CPF ou número de chamada duplicados!", "danger")
            print("[erro editar_aluno]", e)
        finally:
            cursor.close()
            put_conn(conn)

        return redirect(url_for('admin_dashboard'))

    cursor.execute("SELECT id, nome, cpf4, numero_chamada FROM alunos_cadastrados WHERE id=%s", (id,))
    aluno = cursor.fetchone()
    cursor.close()
    put_conn(conn)

    if not aluno:
        flash("Aluno não encontrado!", "danger")
        return redirect(url_for('admin_dashboard'))

    return render_template('editar_aluno.html', aluno=aluno)

# ======================================
# EXCLUIR ALUNO
# ======================================
@app.route('/excluir/<int:id>')
def excluir_aluno(id):
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM alunos_cadastrados WHERE id=%s', (id,))
    conn.commit()
    cursor.close()
    put_conn(conn)
    flash("Aluno excluído com sucesso!", "success")
    return redirect(url_for('admin_dashboard'))

# ======================================
# LOGOUT ADMIN
# ======================================
@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

# ======================================
# GERAR PDF E LIMPAR PRESENÇAS
# ======================================
def gerar_pdf_registros():
    ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    arquivo_pdf = f"registros_{ontem}.pdf"
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT nome, matricula, data_hora, numero_chamada FROM alunos WHERE DATE(data_hora) = %s", (ontem,))
    registros = c.fetchall()
    c.close()
    put_conn(conn)

    if not registros:
        return None

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, f"Registros de Presença - {ontem}", ln=True, align="C")
    pdf.ln(10)
    pdf.set_font("Arial", "", 12)
    for r in registros:
        # r[2] é data_hora (datetime) -> format if needed
        data_hora_str = r[2].strftime("%Y-%m-%d %H:%M:%S") if isinstance(r[2], datetime) else str(r[2])
        linha = f"Nome: {r[0]} | Matrícula: {r[1]} | Chamada: {r[3]} | Horário: {data_hora_str}"
        pdf.cell(0, 10, linha, ln=True)

    pdf.output(arquivo_pdf)
    return arquivo_pdf

@app.route('/baixar_registro')
def baixar_registro():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    arquivo_pdf = f"registros_{ontem}.pdf"
    if not os.path.exists(arquivo_pdf):
        gerar_pdf_registros()

    if os.path.exists(arquivo_pdf):
        return send_file(arquivo_pdf, as_attachment=True)
    else:
        flash("Nenhum registro disponível para download!", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/limpar_presencas')
def limpar_presencas():
    if 'admin' not in session:
        return redirect(url_for('admin_login'))

    hoje = datetime.now().strftime("%Y-%m-%d")
    arquivo_pdf = f"registros_{hoje}.pdf"

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT nome, matricula, data_hora, numero_chamada FROM alunos ORDER BY data_hora ASC")
    registros = c.fetchall()

    if not registros:
        flash("Nenhum registro para salvar ou limpar!", "warning")
        c.close()
        put_conn(conn)
        return redirect(url_for('admin_dashboard'))

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, f"Registros de Presença - {hoje}", ln=True, align="C")
    pdf.ln(10)
    pdf.set_font("Arial", "", 12)
    for r in registros:
        data_hora_str = r[2].strftime("%Y-%m-%d %H:%M:%S") if isinstance(r[2], datetime) else str(r[2])
        linha = f"Nome: {r[0]} | Matrícula: {r[1]} | Chamada: {r[3]} | Horário: {data_hora_str}"
        pdf.cell(0, 10, linha, ln=True)
    pdf.output(arquivo_pdf)

    c.execute("DELETE FROM alunos")
    conn.commit()
    c.close()
    put_conn(conn)

    flash(f"Presenças salvas em '{arquivo_pdf}' e lista zerada com sucesso!", "success")
    return redirect(url_for('admin_dashboard'))

# ======================================
# LIMPEZA AUTOMÁTICA (Sexta 23:59)
# ======================================
def limpar_presencas_automaticamente():
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM alunos")
    conn.commit()
    c.close()
    put_conn(conn)
    print("[🧹] Lista de presença limpa automaticamente!")

def iniciar_agendador():
    schedule.every().friday.at("23:59").do(limpar_presencas_automaticamente)
    while True:
        schedule.run_pending()
        t.sleep(60)

threading.Thread(target=iniciar_agendador, daemon=True).start()

# ======================================
# RODA O APP
# ======================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    try:
        app.run(host="0.0.0.0", port=port)
    finally:
        try:
            close_all_conns()
        except:
            pass
