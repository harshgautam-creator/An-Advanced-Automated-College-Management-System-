from flask import Flask, redirect, render_template, request, session, send_file
import pandas as pd
import mysql.connector as sql
import pdfkit
import os
import qrcode
from datetime import datetime
from PIL import Image
from rembg import remove
import random
from collections import defaultdict

app = Flask(__name__)
app.secret_key = "jarvis_srgpc_secret"

# ==========================================
# GLOBAL VARIABLES & CONFIG
# ==========================================
config = pdfkit.configuration(wkhtmltopdf="/usr/local/bin/wkhtmltopdf") # for linux
#config = pdfkit.configuration(wkhtmltopdf=r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe") # for windows


SEATING_SESSIONS = {}

# ==========================================
# DATABASE CONNECTION (UNIFIED)
# ==========================================
def get_db_connection():
    return sql.connect(host='', user='', password='', database='')

# ==========================================
# UTILITIES
# ==========================================
def remove_white_background(image_path):
    try:
        # Open image
        img = Image.open(image_path)
        # Remove background
        output = remove(img)
        # Convert transparent mode
        output = output.convert("RGBA")
        # Auto crop
        bbox = output.getbbox()
        if bbox:
            output = output.crop(bbox)
        # Save final image
        output.save(image_path)
        print("Signature cleaned successfully ✅")
    except Exception as e:
        print(f"Signature processing error: {e}")

def parse_enrollment(enroll):
    e_str = str(enroll).strip()
    if len(e_str) >= 11:
        # e_str[0:2] = Year (e.g., 24)
        # e_str[5:6] = Branch Alphabet (e.g., C for C04)
        return e_str[0:2], e_str[5:6]
    return "00", "U"

# ==========================================
# NEW ALGORITHM: Frequency-Based Greedy with Diagonal Constraints
# ==========================================
def safe_fill_grid(student_list, rows, benches, students_per_bench):
    total_cols = benches * students_per_bench
    grid = [["EMPTY" for _ in range(total_cols)] for _ in range(rows)]

    # 1. Group students by (Year, Branch)
    groups = defaultdict(list)
    for student in student_list:
        key = parse_enrollment(student)
        groups[key].append(student)

    # Shuffle students within their own groups for randomness
    for key in groups:
        random.shuffle(groups[key])

    # Helper function: Check if placing a student breaks adjacency or diagonal rules
    def is_valid(r, c, student_key):
        # Check Left
        if c > 0 and grid[r][c-1] != "EMPTY":
            if parse_enrollment(grid[r][c-1]) == student_key: return False
        
        # Check Top (Front)
        if r > 0 and grid[r-1][c] != "EMPTY":
            if parse_enrollment(grid[r-1][c]) == student_key: return False
        
        # Check Top-Left (Diagonal)
        if r > 0 and c > 0 and grid[r-1][c-1] != "EMPTY":
            if parse_enrollment(grid[r-1][c-1]) == student_key: return False
            
        # Check Top-Right (Diagonal)
        if r > 0 and c < total_cols - 1 and grid[r-1][c+1] != "EMPTY":
            if parse_enrollment(grid[r-1][c+1]) == student_key: return False
            
        return True

    # 2. Fill the grid seat by seat
    for r in range(rows):
        for c in range(total_cols):
            # Sort the available branches by highest remaining students
            # This ensures we don't end up with a clump of same-branch students at the end
            available_keys = sorted(
                [k for k in groups.keys() if len(groups[k]) > 0],
                key=lambda k: len(groups[k]), 
                reverse=True
            )

            placed = False
            
            # Try to place the student from the largest valid group
            for key in available_keys:
                if is_valid(r, c, key):
                    grid[r][c] = groups[key].pop()
                    placed = True
                    break

            # 3. Fallback Mechanism (Auto-Adjust)
            # Agar koi valid branch nahi mili (kyunki ek hi branch ke bahut bache hain), 
            # toh sabse badi available group ko hi daal do taaki seats khali na rahe.
            if not placed and available_keys:
                fallback_key = available_keys[0]
                grid[r][c] = groups[fallback_key].pop()

    # 4. Partition 2D Array into Bench Structure for HTML
    bench_grid = []
    for r in range(rows):
        bench_row = []
        for b in range(benches):
            start = b * students_per_bench
            end = start + students_per_bench
            bench_row.append(grid[r][start:end])
        bench_grid.append(bench_row)

    return bench_grid


# ==========================================
# ROUTES
# ==========================================
@app.route("/")
def home():
    return render_template("index.html")

# --- ID CARD ROUTES ---
@app.route("/generate-id-card", methods=["POST"])
def generate_id_card():
    data = request.form.to_dict()
    
   # 1. Database Insertion   8-17-25 
    con = get_db_connection()
    cur = con.cursor()
    query = """INSERT INTO student (name, father_name, mother_name, enrollment_no, branch, contact_no, email, aadhar_no, address, dob, alt_contact, samagra_id, study_year) 
     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""
    values = (
    data.get('name'), data.get('father'), data.get('mother'), data.get('enrollment'), 
    data.get('branch'), data.get('contact'), data.get('email'), data.get('adhar'), 
    data.get('address'), data.get('dob'), data.get('alt_contact'), data.get('samagra'),
    data.get('study_year', 1)
    )
    cur.execute(query, values)
    data['ref_no'] = f"{cur.lastrowid:03d}"

    # NEW: ref_no ko wapas student row me save kar do
    cur.execute("UPDATE student SET ref_no=%s WHERE id=%s", (data['ref_no'], cur.lastrowid))

    con.commit()
    con.close()
    # 2. File Handling
    photo = request.files.get("photo")
    sign = request.files.get("student_sign")
    
    for folder in ["uploads", "qr_codes", "saved_pdfs"]:
        os.makedirs(f"static/{folder}", exist_ok=True)
    
    enrollment_str = data['enrollment'].replace("/", "_")
    photo_path = f"static/uploads/p_{enrollment_str}.png"
    sign_path = f"static/uploads/s_{enrollment_str}.png"
    
    if photo: photo.save(photo_path)
    if sign: 
        sign.save(sign_path)
        remove_white_background(sign_path) 

    base_dir = os.path.abspath(os.path.dirname(__file__))
    data['photo'] = "file:///" + os.path.join(base_dir, photo_path).replace("\\", "/")
    data['student_sign'] = "file:///" + os.path.join(base_dir, sign_path).replace("\\", "/")
    data['auth_sign'] = "file:///" + os.path.join(base_dir, "static/auth_sign.png").replace("\\", "/")
    data['logo.left'] = "file:///" + os.path.join(base_dir, "static/logo_left.png").replace("\\", "/")
    data['logo.right'] = "file:///" + os.path.join(base_dir, "static/logo_right.jpg").replace("\\", "/")

    # 3. Validity & QR Code
    try:
        study_year = int(data.get("study_year", 1))
    except ValueError:
        study_year = 1
        
    data['valid_upto'] = datetime.now().year + (4 - study_year)
    data['current_year'] = datetime.now().year
    data['created_date'] = datetime.now().strftime("%d-%m-%Y")
    
    qr_data= f"Name: {data.get('name')}\nFather's Name: {data.get('father')}\nMother's Name: {data.get('mother')}\nEnrollment: {data.get('enrollment')}\nBranch: {data.get('branch')}\nDOB: {data.get('dob')}\nContact NO.: {data.get('contact')}\nEmail ID: {data.get('email')}\nAadhar Card No.: {data.get('adhar')}\nAddress: {data.get('address')}\nAlternate Contact No.: {data.get('alt_contact')}\nSamagra ID: {data.get('samagra')}" 
    
    qr = qrcode.make(qr_data)
    qr_path = f"static/qr_codes/{enrollment_str}.png"
    qr.save(qr_path)
    data['qr_code'] = "file:///" + os.path.join(base_dir, qr_path).replace("\\", "/")

    # 4. Generate SINGLE PDF (ATM Size)
    rendered = render_template("idcard_pdf.html", data=data)
    pdf_path = f"static/saved_pdfs/{enrollment_str}.pdf"
    
    options = {
        'enable-local-file-access': None,
        'page-width': '86mm',  
        'page-height': '54mm', 
        'margin-top': '0mm',
        'margin-right': '0mm',
        'margin-bottom': '0mm',
        'margin-left': '0mm'
    }
    
    pdfkit.from_string(rendered, pdf_path, configuration=config, options=options)
    
    # 5. Save Request For Admin Approval
    con = get_db_connection()
    cur = con.cursor()

    request_query = """
    INSERT INTO requests
    (staff_id, request_type, details, status, pdf_file)
    VALUES (%s, %s, %s, %s, %s)
    """

    details = f"""
    Enrollment: {data.get('enrollment')}
    Name: {data.get('name')}
    Branch: {data.get('branch')}
    Contact: {data.get('contact')}
    """

    request_values = (
    data.get('enrollment'),
    "Student ID Card",
    details,
    "Pending",
    pdf_path
    )

    cur.execute(request_query, request_values)

    con.commit()
    con.close()
    
    return """
    <center style='font-family:Arial;padding-top:100px;'>

    <h1 style='color:green;'>
        Form Submitted Successfully ✅
    </h1>

    <p style='font-size:18px;'>
        Your Information Has Been Submitted Successfully.
        <br><br>
        You will receive your ID card after approval.
    </p>

    <a href='/'
       style='padding:10px 20px;
              background:#007bff;
              color:white;
              text-decoration:none;
              border-radius:5px;'>

        Back To Home

    </a>

</center>
"""

# --- STUDENT LOGIN SYSTEM ---
@app.route("/student-login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        enrollment = request.form.get("enrollment")
        password = request.form.get("password")

        con = get_db_connection()
        cur = con.cursor(dictionary=True)
        cur.execute("SELECT * FROM student_login WHERE enrollment_no=%s AND password=%s", (enrollment, password))
        user = cur.fetchone()
        con.close()

        if user:
            session["student_user"] = enrollment
            return redirect("/id-form")
        return "Invalid Enrollment or Password"
    return render_template("student_login.html")


@app.route('/student-forgot-password', methods=['GET', 'POST'])
def student_forgot_password():
    message = ""

    if request.method == 'POST':
        enrollment = request.form.get('enrollment')
        new_password = request.form.get('new_password')

        con = get_db_connection()
        cur = con.cursor(dictionary=True)

        # Check student exists or not
        cur.execute(
            "SELECT * FROM student_login WHERE enrollment_no=%s",
            (enrollment,)
        )

        user = cur.fetchone()

        if user:
            cur.execute(
                "UPDATE student_login SET password=%s WHERE enrollment_no=%s",
                (new_password, enrollment)
            )
            con.commit()
            message = "Password updated successfully ✅"
        else:
            message = "Enrollment number not found ❌"

        con.close()

    return render_template(
        'forgot_password.html',
        message=message
    )

@app.route("/student-signup", methods=["GET", "POST"])
def student_signup():
    if request.method == "POST":
        enrollment = request.form.get("enrollment")
        password = request.form.get("password")
        try:
            con = get_db_connection()
            cur = con.cursor()
            cur.execute("INSERT INTO student_login(enrollment_no,password) VALUES(%s,%s)", (enrollment, password))
            con.commit()
            con.close()
            return redirect("/student-login")
        except Exception as e:
            return f"Already Registered or Error: {e}"
    return render_template("student_signup.html")

@app.route("/id-form")
def id_form():
    if "student_user" not in session:
        return redirect("/student-login")
    return render_template("id_form.html")

@app.route("/student-logout")
def student_logout():
    session.pop("student_user", None)
    return redirect("/student-login")

# --- SEATING ARRANGEMENT ROUTES ---
@app.route("/seating-login", methods=["GET", "POST"])
def seating_login():
    if request.method == "POST":
        staff_id = request.form.get("staff_id")
        password = request.form.get("password")

        con = get_db_connection()
        cur = con.cursor(dictionary=True)
        cur.execute("SELECT * FROM staff WHERE staff_id=%s AND password=%s", (staff_id, password))
        user = cur.fetchone()
        con.close()

        if user:
            session["user"] = staff_id
            return redirect("/dashboard")

        return "Invalid ID or Password"
    return render_template("seating_login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name")
        staff_id = request.form.get("staff_id")
        password = request.form.get("password")

        try:
            con = get_db_connection()
            cur = con.cursor()

            details = f"""
            Name: {name}
            Password: {password}
            """

            # Save signup request
            cur.execute(
                """
                INSERT INTO requests
                (staff_id, request_type, details, status)
                VALUES (%s,%s,%s,%s)
                """,
                (
                    staff_id,
                    "Faculty Signup",
                    details,
                    "Pending"
                )
            )

            con.commit()
            con.close()

            return """
            <center style='padding-top:100px;font-family:Arial;'>

                <h1 style='color:green;'>
                    Signup Request Sent ✅
                </h1>

                <p>
                    Your account request has been sent to admin.
                    <br><br>
                    Please wait for approval.
                </p>

                <a href='/'>
                    Back To Home
                </a>

            </center>
            """

        except Exception as e:
            return f"Error: {e}"

    return render_template("signup.html")

@app.route('/staff-forgot-password', methods=['GET', 'POST'])
def staff_forgot_password():
    message = ""
    if request.method == 'POST':
        staff_id = request.form.get('staff_id')
        new_password = request.form.get('new_password')

        con = get_db_connection()
        cur = con.cursor(dictionary=True)

        cur.execute(
            "SELECT * FROM staff WHERE staff_id=%s",
            (staff_id,)
        )

        user = cur.fetchone()

        if user:
            cur.execute(
                "UPDATE staff SET password=%s WHERE staff_id=%s",
                (new_password, staff_id)
            )
            con.commit()
            message = "Password updated successfully ✅"
        else:
            message = "Staff ID not found ❌"

        con.close()

    return render_template(
        'staff_forgot_password.html',
        message=message
    )
    
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/seating-login")
    return render_template("dashboard.html")

@app.route("/count-excel", methods=["POST"])
def count_excel():
    try:
        file = request.files["excelFile"]
        df = pd.read_excel(file)
        if "Enrollment Number" not in df.columns:
            return {"count": 0}
        total = len(df["Enrollment Number"].dropna())
        return {"count": total}
    except:
        return {"count": 0}

@app.route("/setup-seating", methods=["POST"])
def setup_seating():
    if "user" not in session: return redirect("/seating-login")
    file = request.files["excelFile"]
    df = pd.read_excel(file)
    students = df["Enrollment Number"].dropna().astype(str).tolist()

    groups = {}
    for enroll in students:
        yr, br = parse_enrollment(enroll)
        key = f"{yr}_{br}"
        groups.setdefault(key, []).append(enroll)

    mixed_students = []
    while True:
        added = False
        for key in list(groups.keys()):
            if groups[key]:
                mixed_students.append(groups[key].pop(0))
                added = True
        if not added: break

    user_id = session["user"]
    SEATING_SESSIONS[user_id] = {
        "remaining_students": mixed_students,
        "total_students": len(mixed_students),
        "rooms": []
    }
    return redirect("/assign-room")

@app.route("/assign-room", methods=["GET", "POST"])
def assign_room():
    if "user" not in session: return redirect("/seating-login")
    user_id = session["user"]
    if user_id not in SEATING_SESSIONS: return redirect("/dashboard")

    session_data = SEATING_SESSIONS[user_id]
    remaining = session_data["remaining_students"]

    if request.method == "POST":
        room_number = request.form.get("room_number")
        rows = int(request.form.get("rows"))
        columns = int(request.form.get("columns"))
        students_per_bench = int(request.form.get("students_per_bench", 1))

        capacity = rows * columns * students_per_bench
        room_students = remaining[:capacity]
        SEATING_SESSIONS[user_id]["remaining_students"] = remaining[capacity:]

        # Call to the new algorithm
        grid = safe_fill_grid(room_students, rows, columns, students_per_bench)
        
        SEATING_SESSIONS[user_id]["rooms"].append({
            "room_no": room_number,
            "count": len(room_students),
            "seating": grid,
            "benches_per_row": columns,
            "students_per_bench": students_per_bench
        })

        if len(SEATING_SESSIONS[user_id]["remaining_students"]) > 0:
            return redirect("/assign-room")
        else:
            return redirect("/seating-result")

    return render_template("assign_room.html", remaining_count=len(remaining))

@app.route("/seating-result")
def seating_result():
    if "user" not in session: return redirect("/seating-login")
    user_id = session["user"]
    if user_id not in SEATING_SESSIONS: return redirect("/dashboard")
    return render_template("seating_result.html", rooms=SEATING_SESSIONS[user_id]["rooms"], total_students=SEATING_SESSIONS[user_id]["total_students"])
    
@app.route('/send_seating_request')
def send_seating_request():
    if "user" not in session: return redirect("/seating-login")
    user_id = session["user"]
    if user_id not in SEATING_SESSIONS: return redirect("/dashboard")

    session_data = SEATING_SESSIONS[user_id]
    os.makedirs("static/reports", exist_ok=True)
    filename = f"seating_{user_id}.pdf"
    filepath = os.path.join("static", "reports", filename).replace("\\", "/")

    rendered = render_template(
        "seating_result.html", 
        rooms=session_data["rooms"], 
        total_students=session_data["total_students"], 
        for_admin=True
    )
    
    html_path = os.path.join("static", "reports", f"seating_{user_id}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(rendered)

    options = {"enable-local-file-access": None}
    pdfkit.from_file(html_path, filepath, configuration=config, options=options)

    con = get_db_connection()
    cur = con.cursor()
    cur.execute("""INSERT INTO requests (staff_id, request_type, details, pdf_file, status) VALUES (%s,%s,%s,%s,%s)""", 
                (user_id, "Seating", "Seating arrangement ready for approval", filepath, "Pending"))
    con.commit()
    con.close()
    return redirect("/dashboard")

   
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

# --- ADMIN PANEL ---
@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == "admin@srgpc" and password == "srgpc1986":
            session['admin'] = True
            return redirect('/admin_dashboard')
        else:
            return render_template("admin_login.html", error="Wrong Username or Password")
    return render_template("admin_login.html")

@app.route('/admin_dashboard')
def admin_dashboard():
    if 'admin' not in session: return redirect('/admin')

    con = get_db_connection()
    cursor = con.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) AS total FROM staff")
    total_staff = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(*) AS total FROM student")
    total_students = cursor.fetchone()['total']
    cursor.execute("SELECT COUNT(*) AS total FROM requests WHERE status='Pending'")
    pending_requests = cursor.fetchone()['total']
    cursor.execute("SELECT * FROM requests ORDER BY id DESC")
    requests_data = cursor.fetchall()
    
    con.close()

    return render_template("admin_dashboard.html", total_staff=total_staff, total_students=total_students, pending_requests=pending_requests, requests_data=requests_data)

@app.route('/admin_logout')
def admin_logout():
    session.pop('admin', None)
    return redirect('/admin')

@app.route('/approve_request/<int:req_id>')
def approve_request(req_id):
    if 'admin' not in session:
        return redirect('/admin')

    con = get_db_connection()
    cur = con.cursor(dictionary=True)

    # Get request data first
    cur.execute(
        "SELECT * FROM requests WHERE id=%s",
        (req_id,)
    )

    row = cur.fetchone()

    # Approve request
    cur.execute(
        "UPDATE requests SET status='Approved' WHERE id=%s",
        (req_id,)
    )

    # ==============================
    # FACULTY SIGNUP APPROVAL
    # ==============================
    if row['request_type'] == "Faculty Signup":
        details = row['details']
        lines = details.strip().split("\n")
        name = lines[0].replace("Name: ", "").strip()
        password = lines[1].replace("Password: ", "").strip()

        # Create staff account
        cur.execute(
            """
            INSERT INTO staff(name, staff_id, password)
            VALUES(%s,%s,%s)
            """,
            (
                name,
                row['staff_id'],
                password
            )
        )

        con.commit()
        con.close()
        return redirect('/admin_dashboard')

    # ==============================
    # PDF REQUESTS
    # ==============================
    con.commit()
    con.close()

    pdf_path = row['pdf_file'].replace("\\", "/")

    return f"""
    <script>
        window.location='/{pdf_path}';
    </script>
    """
  
@app.route('/reject_request/<int:req_id>')
def reject_request(req_id):
    if 'admin' not in session: return redirect('/admin')
    con = get_db_connection()
    cursor = con.cursor()
    cursor.execute("UPDATE requests SET status='Rejected' WHERE id=%s", (req_id,))
    con.commit()
    con.close()
    return redirect('/admin_dashboard')

# NEW: BATCH ID CARD PRINTING 08-17-2026
@app.route('/print-id-cards')
def print_id_cards():
    if 'admin' not in session:
        return redirect('/admin')

    con = get_db_connection()
    cur = con.cursor(dictionary=True)
    cur.execute("""
        SELECT s.*, r.id AS req_id, r.status
        FROM requests r
        JOIN student s ON s.enrollment_no = r.staff_id
        WHERE r.request_type = 'Student ID Card' AND r.status = 'Approved'
        ORDER BY r.id ASC
    """)
    rows = cur.fetchall()
    con.close()

    base_dir = os.path.abspath(os.path.dirname(__file__))
    students = []

    for row in rows:
        enrollment_str = str(row['enrollment_no']).replace("/", "_")
        d = dict(row)

        d['enrollment'] = row['enrollment_no']
        d['father'] = row['father_name']
        d['mother'] = row['mother_name']
        d['adhar'] = row['aadhar_no']

        d['photo'] = "file:///" + os.path.join(base_dir, f"static/uploads/p_{enrollment_str}.png").replace("\\", "/")
        d['student_sign'] = "file:///" + os.path.join(base_dir, f"static/uploads/s_{enrollment_str}.png").replace("\\", "/")
        d['auth_sign'] = "file:///" + os.path.join(base_dir, "static/auth_sign.png").replace("\\", "/")
        d['logo.left'] = "file:///" + os.path.join(base_dir, "static/logo_left.png").replace("\\", "/")
        d['logo.right'] = "file:///" + os.path.join(base_dir, "static/logo_right.jpg").replace("\\", "/")
        d['qr_code'] = "file:///" + os.path.join(base_dir, f"static/qr_codes/{enrollment_str}.png").replace("\\", "/")

        d['ref_no'] = row.get('ref_no') or f"{row['id']:03d}"
        d['current_year'] = datetime.now().year
        d['created_date'] = datetime.now().strftime("%d-%m-%Y")
        study_year = row.get('study_year') or 1
        d['valid_upto'] = datetime.now().year + (4 - study_year)

        students.append(d)

    # Pehle students ko 2-2 ke "rows" me todo
    student_rows = [students[i:i + 2] for i in range(0, len(students), 2)]
    # Phir har 3 rows ka ek "page" banao (3 rows x 2 cards = 6 cards per page)
    pages = [student_rows[i:i + 3] for i in range(0, len(student_rows), 3)]

    rendered = render_template("idcard_batch_pdf.html", pages=pages)

    os.makedirs("static/saved_pdfs", exist_ok=True)
    pdf_path = "static/saved_pdfs/batch_id_cards.pdf"

    options = {
        'enable-local-file-access': None,
        'page-size': 'A4',
        'margin-top': '0mm',
        'margin-bottom': '0mm',
        'margin-left': '0mm',
        'margin-right': '0mm',
        'disable-smart-shrinking': None,
        'dpi': '96',
        'zoom': '1',
    }
    pdfkit.from_string(rendered, pdf_path, configuration=config, options=options)

    return send_file(pdf_path, as_attachment=False)

if __name__ == "__main__":
    app.run(debug=True)
