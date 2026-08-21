import asyncio
import json
from datetime import datetime, date, timedelta
from aiohttp import web
from dotenv import load_dotenv

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import appointments_db
import config

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Healthcare Clinic - Appointment Live Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-hover: #334155;
            --border: #334155;
            --primary: #3b82f6;
            --primary-glow: rgba(59, 130, 246, 0.2);
            --success: #10b981;
            --success-bg: rgba(16, 185, 129, 0.15);
            --danger: #ef4444;
            --danger-bg: rgba(239, 68, 68, 0.15);
            --text: #f8fafc;
            --text-muted: #94a3b8;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        body {
            background-color: var(--bg);
            color: var(--text);
            padding: 2rem;
            min-height: 100vh;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            padding-bottom: 1.5rem;
            border-bottom: 1px solid var(--border);
        }

        .header-title h1 {
            font-size: 1.8rem;
            font-weight: 700;
            color: #fff;
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .header-title p {
            color: var(--text-muted);
            font-size: 0.95rem;
            margin-top: 0.25rem;
        }

        .live-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            background: rgba(16, 185, 129, 0.15);
            color: var(--success);
            padding: 0.4rem 0.85rem;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 600;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .pulse-dot {
            width: 8px;
            height: 8px;
            background-color: var(--success);
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }

        @keyframes pulse {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1.25rem;
            margin-bottom: 2rem;
        }

        .stat-card {
            background: var(--surface);
            padding: 1.25rem;
            border-radius: 12px;
            border: 1px solid var(--border);
        }

        .stat-card .label {
            color: var(--text-muted);
            font-size: 0.85rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .stat-card .value {
            font-size: 2rem;
            font-weight: 700;
            margin-top: 0.5rem;
            color: #fff;
        }

        .controls {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
            gap: 1rem;
            flex-wrap: wrap;
        }

        .search-box {
            position: relative;
            flex: 1;
            max-width: 400px;
        }

        .search-box input {
            width: 100%;
            padding: 0.75rem 1rem 0.75rem 2.5rem;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            color: #fff;
            font-size: 0.95rem;
            outline: none;
            transition: border-color 0.2s;
        }

        .search-box input:focus {
            border-color: var(--primary);
            box-shadow: 0 0 0 2px var(--primary-glow);
        }

        .search-icon {
            position: absolute;
            left: 0.85rem;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            font-size: 1rem;
        }

        .filter-buttons {
            display: flex;
            gap: 0.5rem;
        }

        .filter-btn {
            background: var(--surface);
            color: var(--text-muted);
            border: 1px solid var(--border);
            padding: 0.65rem 1.2rem;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.9rem;
            font-weight: 500;
            transition: all 0.2s;
        }

        .filter-btn.active, .filter-btn:hover {
            background: var(--primary);
            color: #fff;
            border-color: var(--primary);
        }

        .table-card {
            background: var(--surface);
            border-radius: 12px;
            border: 1px solid var(--border);
            overflow: hidden;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        thead {
            background: rgba(15, 23, 42, 0.6);
            border-bottom: 1px solid var(--border);
        }

        th {
            padding: 1.1rem 1.25rem;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        td {
            padding: 1.25rem;
            font-size: 1.05rem;
            color: var(--text);
            border-bottom: 1px solid rgba(51, 65, 85, 0.5);
        }

        tbody tr:hover {
            background: rgba(51, 65, 85, 0.3);
        }

        .patient-name {
            font-weight: 600;
            color: #fff;
            font-size: 1.15rem;
        }

        .phone-badge {
            font-family: monospace;
            font-size: 1.05rem;
            color: #cbd5e1;
            letter-spacing: 0.05em;
        }

        .time-badge {
            background: rgba(59, 130, 246, 0.15);
            color: #93c5fd;
            padding: 0.35rem 0.75rem;
            border-radius: 6px;
            font-weight: 600;
            display: inline-block;
            font-size: 1rem;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            padding: 0.35rem 0.85rem;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 600;
        }

        .status-booked {
            background: var(--success-bg);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .status-cancelled {
            background: var(--danger-bg);
            color: var(--danger);
            border: 1px solid rgba(239, 68, 68, 0.3);
        }

        .action-btn {
            background: rgba(239, 68, 68, 0.1);
            color: #f87171;
            border: 1px solid rgba(239, 68, 68, 0.2);
            padding: 0.4rem 0.85rem;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85rem;
            font-weight: 500;
            transition: all 0.2s;
        }

        .action-btn:hover {
            background: var(--danger);
            color: #fff;
        }

        .empty-state {
            text-align: center;
            padding: 3rem;
            color: var(--text-muted);
            font-size: 1.1rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-title">
                <h1>🏥 Healthcare Clinic <span>•</span> Live Appointments</h1>
                <p>Dr. Umang Chourasia (General Physician) • Consultation Fee: ₹500</p>
            </div>
            <div class="live-badge">
                <div class="pulse-dot"></div>
                LIVE DATABASE SYNC
            </div>
        </header>

        <div class="stats-grid">
            <div class="stat-card">
                <div class="label">Total Booked (All)</div>
                <div class="value" id="stat-total-booked">-</div>
            </div>
            <div class="stat-card">
                <div class="label">Tomorrow's Bookings</div>
                <div class="value" style="color: #60a5fa;" id="stat-tomorrow">-</div>
            </div>
            <div class="stat-card">
                <div class="label">Today's Bookings</div>
                <div class="value" style="color: #34d399;" id="stat-today">-</div>
            </div>
            <div class="stat-card">
                <div class="label">Cancelled Records</div>
                <div class="value" style="color: #f87171;" id="stat-cancelled">-</div>
            </div>
        </div>

        <div class="controls">
            <div class="search-box">
                <span class="search-icon">🔍</span>
                <input type="text" id="searchInput" placeholder="Search by patient name, phone, or time..." oninput="filterAppointments()">
            </div>
            <div class="filter-buttons">
                <button class="filter-btn active" onclick="setFilter('active')">Active Booked</button>
                <button class="filter-btn" onclick="setFilter('all')">All Records</button>
                <button class="filter-btn" onclick="setFilter('tomorrow')">Tomorrow Only</button>
                <button class="filter-btn" onclick="setFilter('cancelled')">Cancelled</button>
            </div>
        </div>

        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Patient Name</th>
                        <th>Contact Phone</th>
                        <th>Appointment Date</th>
                        <th>Time Slot</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="appointmentTableBody">
                    <tr><td colspan="7" class="empty-state">Loading appointments from PostgreSQL...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let allAppointments = [];
        let currentFilter = 'active';

        async function fetchAppointments() {
            try {
                const res = await fetch('/api/appointments');
                const data = await res.json();
                allAppointments = data;
                updateStats();
                renderAppointments();
            } catch (err) {
                console.error("Error fetching appointments:", err);
            }
        }

        function updateStats() {
            const todayStr = new Date().toISOString().split('T')[0];
            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            const tomorrowStr = tomorrow.toISOString().split('T')[0];

            const booked = allAppointments.filter(a => a.status === 'BOOKED');
            const cancelled = allAppointments.filter(a => a.status === 'CANCELLED');
            const todayBooked = booked.filter(a => a.date === todayStr);
            const tomorrowBooked = booked.filter(a => a.date === tomorrowStr);

            document.getElementById('stat-total-booked').textContent = booked.length;
            document.getElementById('stat-tomorrow').textContent = tomorrowBooked.length;
            document.getElementById('stat-today').textContent = todayBooked.length;
            document.getElementById('stat-cancelled').textContent = cancelled.length;
        }

        function setFilter(filter) {
            currentFilter = filter;
            document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            renderAppointments();
        }

        function filterAppointments() {
            renderAppointments();
        }

        function renderAppointments() {
            const query = document.getElementById('searchInput').value.toLowerCase();
            const todayStr = new Date().toISOString().split('T')[0];
            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            const tomorrowStr = tomorrow.toISOString().split('T')[0];

            let filtered = allAppointments.filter(a => {
                if (currentFilter === 'active' && a.status !== 'BOOKED') return false;
                if (currentFilter === 'cancelled' && a.status !== 'CANCELLED') return false;
                if (currentFilter === 'tomorrow' && (a.date !== tomorrowStr || a.status !== 'BOOKED')) return false;

                if (query) {
                    const matchName = (a.name || '').toLowerCase().includes(query);
                    const matchPhone = (a.phone || '').toLowerCase().includes(query);
                    const matchTime = (a.time || '').toLowerCase().includes(query);
                    const matchDate = (a.date || '').toLowerCase().includes(query);
                    return matchName || matchPhone || matchTime || matchDate;
                }
                return true;
            });

            const tbody = document.getElementById('appointmentTableBody');
            if (filtered.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No appointments found matching your criteria.</td></tr>';
                return;
            }

            tbody.innerHTML = filtered.map(a => `
                <tr>
                    <td style="color: #64748b; font-weight: 600;">#${a.id}</td>
                    <td><div class="patient-name">${escapeHtml(a.name || 'Anonymous')}</div></td>
                    <td><span class="phone-badge">${escapeHtml(formatPhone(a.phone))}</span></td>
                    <td><strong>${a.date}</strong></td>
                    <td><span class="time-badge">🕒 ${formatTime(a.time)}</span></td>
                    <td>
                        <span class="status-badge ${a.status === 'BOOKED' ? 'status-booked' : 'status-cancelled'}">
                            ${a.status === 'BOOKED' ? '● Active Booked' : '✕ Cancelled'}
                        </span>
                    </td>
                    <td>
                        ${a.status === 'BOOKED' ? `
                            <button class="action-btn" onclick="cancelAppointment(${a.id})">Cancel</button>
                        ` : '<span style="color: #64748b; font-size: 0.85rem;">None</span>'}
                    </td>
                </tr>
            `).join('');
        }

        function formatPhone(p) {
            if (!p) return '-';
            const digits = p.replace(/\\D/g, '');
            if (digits.length === 10) {
                return digits.replace(/(\\d{5})(\\d{5})/, '$1 $2');
            }
            return p;
        }

        function formatTime(t) {
            if (!t) return '-';
            const parts = t.split(':');
            let h = parseInt(parts[0]);
            const m = parts[1];
            const ampm = h >= 12 ? 'PM' : 'AM';
            h = h % 12;
            h = h ? h : 12;
            return `${h}:${m} ${ampm}`;
        }

        async function cancelAppointment(id) {
            if (!confirm(`Are you sure you want to cancel appointment #${id}?`)) return;
            try {
                const res = await fetch(`/api/cancel/${id}`, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    fetchAppointments();
                } else {
                    alert(data.message || 'Failed to cancel');
                }
            } catch (err) {
                alert('Network error while cancelling');
            }
        }

        function escapeHtml(str) {
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        fetchAppointments();
        setInterval(fetchAppointments, 3000);
    </script>
</body>
</html>
"""

async def index(request):
    return web.Response(text=HTML_TEMPLATE, content_type='text/html')

async def get_appointments_api(request):
    with appointments_db.get_db_cursor() as cur:
        cur.execute("SELECT id, patient_name, patient_phone, appointment_date, appointment_time, status FROM appointments ORDER BY appointment_date DESC, appointment_time DESC")
        rows = cur.fetchall()
    
    data = []
    for r in rows:
        aid, name, phone, dt, tm, status = r
        time_str = tm.strftime("%H:%M") if hasattr(tm, "strftime") else str(tm)[:5]
        data.append({
            "id": aid,
            "name": name,
            "phone": phone,
            "date": str(dt),
            "time": time_str,
            "status": status
        })
    return web.json_response(data)

async def cancel_api(request):
    appt_id = int(request.match_info['id'])
    with appointments_db.get_db_cursor() as cur:
        cur.execute("UPDATE appointments SET status = 'CANCELLED' WHERE id = %s RETURNING id", (appt_id,))
        row = cur.fetchone()
    if row:
        return web.json_response({"success": True, "message": "Appointment cancelled"})
    return web.json_response({"success": False, "message": "Appointment not found"}, status=404)

async def main():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/api/appointments', get_appointments_api)
    app.router.add_post('/api/cancel/{id}', cancel_api)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 5000)
    print("Healthcare Clinic Live Dashboard running on http://localhost:5000")
    await site.start()
    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
