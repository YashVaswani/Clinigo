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
    <title>Healthcare Clinic - AI Reception Command Center</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #0a0f1d;
            --bg-surface: #111827;
            --bg-surface-elevated: #1e293b;
            --bg-card: rgba(17, 24, 39, 0.75);
            --border: rgba(255, 255, 255, 0.08);
            --border-hover: rgba(255, 255, 255, 0.18);
            
            --primary: #3b82f6;
            --primary-glow: rgba(59, 130, 246, 0.35);
            --cyan: #06b6d4;
            --cyan-glow: rgba(6, 182, 212, 0.3);
            --emerald: #10b981;
            --emerald-bg: rgba(16, 185, 129, 0.12);
            --amber: #f59e0b;
            --amber-bg: rgba(245, 158, 11, 0.12);
            --rose: #f43f5e;
            --rose-bg: rgba(244, 63, 94, 0.12);
            --purple: #8b5cf6;
            
            --text-main: #f8fafc;
            --text-secondary: #94a3b8;
            --text-tertiary: #64748b;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
        }

        body {
            background-color: var(--bg-base);
            background-image: 
                radial-gradient(at 0% 0%, rgba(59, 130, 246, 0.12) 0px, transparent 50%),
                radial-gradient(at 100% 0%, rgba(139, 92, 246, 0.1) 0px, transparent 50%),
                radial-gradient(at 50% 100%, rgba(6, 182, 212, 0.08) 0px, transparent 50%);
            background-attachment: fixed;
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }

        .dashboard-container {
            max-width: 1400px;
            width: 100%;
            margin: 0 auto;
            padding: 2rem;
            flex: 1;
        }

        /* Top Header Navigation */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1.25rem 1.75rem;
            background: var(--bg-card);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            border-radius: 16px;
            margin-bottom: 2rem;
            box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
        }

        .brand-area {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .brand-logo {
            width: 44px;
            height: 44px;
            background: linear-gradient(135deg, var(--primary), var(--cyan));
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.5rem;
            box-shadow: 0 0 20px var(--primary-glow);
        }

        .brand-text h1 {
            font-size: 1.35rem;
            font-weight: 700;
            color: #fff;
            letter-spacing: -0.02em;
        }

        .brand-text p {
            font-size: 0.82rem;
            color: var(--text-secondary);
            margin-top: 2px;
        }

        .header-actions {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .live-status-pill {
            display: flex;
            align-items: center;
            gap: 0.6rem;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: var(--emerald);
            padding: 0.5rem 1rem;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 600;
        }

        .pulse-indicator {
            width: 8px;
            height: 8px;
            background: var(--emerald);
            border-radius: 50%;
            box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
            animation: pulse-ring 2s infinite;
        }

        @keyframes pulse-ring {
            0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1); box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        .btn-new-appointment {
            background: linear-gradient(135deg, var(--primary), #2563eb);
            color: #fff;
            border: none;
            padding: 0.65rem 1.25rem;
            border-radius: 10px;
            font-weight: 600;
            font-size: 0.9rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            box-shadow: 0 4px 15px var(--primary-glow);
            transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }

        .btn-new-appointment:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px var(--primary-glow);
        }

        /* Stats KPI Bar */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1.25rem;
            margin-bottom: 2rem;
        }

        .kpi-card {
            background: var(--bg-card);
            backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
            transition: all 0.25s ease;
        }

        .kpi-card:hover {
            border-color: var(--border-hover);
            transform: translateY(-2px);
        }

        .kpi-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 3px;
            background: linear-gradient(90deg, var(--primary), transparent);
        }

        .kpi-card.kpi-emerald::before { background: linear-gradient(90deg, var(--emerald), transparent); }
        .kpi-card.kpi-cyan::before { background: linear-gradient(90deg, var(--cyan), transparent); }
        .kpi-card.kpi-purple::before { background: linear-gradient(90deg, var(--purple), transparent); }

        .kpi-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .kpi-title {
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .kpi-icon {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            background: rgba(255, 255, 255, 0.05);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.15rem;
        }

        .kpi-value {
            font-size: 2.2rem;
            font-weight: 800;
            color: #fff;
            margin: 0.6rem 0 0.3rem 0;
            letter-spacing: -0.03em;
        }

        .kpi-subtext {
            font-size: 0.8rem;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 0.4rem;
        }

        /* Main Tabs Navigation */
        .tab-nav {
            display: flex;
            gap: 0.5rem;
            background: rgba(17, 24, 39, 0.6);
            padding: 0.4rem;
            border-radius: 12px;
            border: 1px solid var(--border);
            margin-bottom: 1.5rem;
            width: fit-content;
        }

        .tab-btn {
            background: transparent;
            color: var(--text-secondary);
            border: none;
            padding: 0.65rem 1.4rem;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.9rem;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            transition: all 0.2s ease;
        }

        .tab-btn.active {
            background: var(--bg-surface-elevated);
            color: #fff;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        }

        .tab-btn:hover:not(.active) {
            color: #fff;
            background: rgba(255, 255, 255, 0.04);
        }

        /* Tab Content Panes */
        .tab-pane {
            display: none;
        }

        .tab-pane.active {
            display: block;
            animation: fadeIn 0.25s ease-out;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(6px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* Section Container Card */
        .content-card {
            background: var(--bg-card);
            backdrop-filter: blur(16px);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 1.75rem;
            box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.5);
        }

        /* Table Filter Controls */
        .table-controls {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 1rem;
            margin-bottom: 1.5rem;
            flex-wrap: wrap;
        }

        .search-bar {
            position: relative;
            flex: 1;
            max-width: 420px;
        }

        .search-bar input {
            width: 100%;
            padding: 0.75rem 1rem 0.75rem 2.75rem;
            background: var(--bg-surface-elevated);
            border: 1px solid var(--border);
            border-radius: 10px;
            color: #fff;
            font-size: 0.95rem;
            outline: none;
            transition: all 0.2s;
        }

        .search-bar input:focus {
            border-color: var(--primary);
            box-shadow: 0 0 0 3px var(--primary-glow);
        }

        .search-icon-svg {
            position: absolute;
            left: 1rem;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-tertiary);
            width: 18px;
            height: 18px;
        }

        .filter-group {
            display: flex;
            gap: 0.4rem;
            background: var(--bg-surface);
            padding: 0.3rem;
            border-radius: 10px;
            border: 1px solid var(--border);
        }

        .filter-chip {
            background: transparent;
            color: var(--text-secondary);
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }

        .filter-chip.active {
            background: var(--primary);
            color: #fff;
        }

        /* Modern Table */
        .table-wrapper {
            overflow-x: auto;
            border-radius: 12px;
            border: 1px solid var(--border);
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        thead {
            background: rgba(15, 23, 42, 0.8);
            border-bottom: 1px solid var(--border);
        }

        th {
            padding: 1rem 1.25rem;
            font-size: 0.8rem;
            font-weight: 700;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.06em;
        }

        td {
            padding: 1.15rem 1.25rem;
            font-size: 0.95rem;
            color: var(--text-main);
            border-bottom: 1px solid var(--border);
            vertical-align: middle;
        }

        tbody tr:hover {
            background: rgba(255, 255, 255, 0.02);
        }

        .patient-cell {
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }

        .patient-avatar {
            width: 38px;
            height: 38px;
            border-radius: 10px;
            background: linear-gradient(135deg, rgba(59, 130, 246, 0.2), rgba(6, 182, 212, 0.2));
            border: 1px solid rgba(59, 130, 246, 0.3);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            color: var(--cyan);
            font-size: 0.95rem;
        }

        .patient-name-text {
            font-weight: 600;
            color: #fff;
            font-size: 1rem;
        }

        .phone-tag {
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-secondary);
            font-size: 0.9rem;
            background: rgba(255, 255, 255, 0.04);
            padding: 0.25rem 0.6rem;
            border-radius: 6px;
            border: 1px solid rgba(255, 255, 255, 0.06);
        }

        .time-pill {
            background: rgba(59, 130, 246, 0.12);
            color: #93c5fd;
            border: 1px solid rgba(59, 130, 246, 0.25);
            padding: 0.35rem 0.75rem;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.88rem;
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
        }

        .badge-status {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.35rem 0.8rem;
            border-radius: 9999px;
            font-size: 0.82rem;
            font-weight: 600;
        }

        .badge-booked {
            background: var(--emerald-bg);
            color: var(--emerald);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-cancelled {
            background: var(--rose-bg);
            color: var(--rose);
            border: 1px solid rgba(244, 63, 94, 0.3);
        }

        .badge-completed {
            background: rgba(59, 130, 246, 0.12);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.3);
        }

        .btn-cancel-action {
            background: rgba(244, 63, 94, 0.1);
            color: #fb7185;
            border: 1px solid rgba(244, 63, 94, 0.2);
            padding: 0.45rem 0.9rem;
            border-radius: 8px;
            font-weight: 600;
            font-size: 0.85rem;
            cursor: pointer;
            transition: all 0.2s;
        }

        .btn-cancel-action:hover {
            background: var(--rose);
            color: #fff;
        }

        /* Slot Matrix Grid */
        .matrix-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1.5rem;
        }

        .date-picker-input {
            background: var(--bg-surface-elevated);
            color: #fff;
            border: 1px solid var(--border);
            padding: 0.6rem 1rem;
            border-radius: 10px;
            font-size: 0.95rem;
            outline: none;
            cursor: pointer;
        }

        .slot-grid-container {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
            gap: 0.85rem;
        }

        .slot-box {
            background: var(--bg-surface-elevated);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1rem;
            text-align: center;
            transition: all 0.2s ease;
            position: relative;
        }

        .slot-box.available {
            border-color: rgba(16, 185, 129, 0.3);
            background: rgba(16, 185, 129, 0.06);
            cursor: pointer;
        }

        .slot-box.available:hover {
            background: rgba(16, 185, 129, 0.15);
            transform: translateY(-2px);
        }

        .slot-box.booked {
            border-color: rgba(59, 130, 246, 0.3);
            background: rgba(59, 130, 246, 0.1);
        }

        .slot-time {
            font-weight: 700;
            font-size: 1rem;
            color: #fff;
        }

        .slot-status-text {
            font-size: 0.75rem;
            font-weight: 600;
            margin-top: 0.35rem;
            text-transform: uppercase;
        }

        .slot-box.available .slot-status-text { color: var(--emerald); }
        .slot-box.booked .slot-status-text { color: #93c5fd; }

        /* Call Sessions List */
        .call-card {
            background: var(--bg-surface-elevated);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 1.25rem;
            margin-bottom: 1rem;
            transition: all 0.2s;
        }

        .call-card:hover {
            border-color: var(--border-hover);
        }

        .call-card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .call-info-left {
            display: flex;
            align-items: center;
            gap: 1rem;
        }

        .call-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: rgba(59, 130, 246, 0.15);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.25rem;
            color: var(--primary);
        }

        .call-phone {
            font-weight: 700;
            font-size: 1.05rem;
            color: #fff;
        }

        .call-time-meta {
            font-size: 0.8rem;
            color: var(--text-secondary);
            margin-top: 2px;
        }

        .call-transcript-drawer {
            background: rgba(10, 15, 29, 0.6);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 1rem;
            margin-top: 1rem;
            font-size: 0.9rem;
            line-height: 1.6;
            color: #cbd5e1;
            white-space: pre-wrap;
            max-height: 250px;
            overflow-y: auto;
        }

        /* Architecture System Status */
        .arch-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 1.5rem;
        }

        .arch-card {
            background: var(--bg-surface-elevated);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 1.5rem;
        }

        .arch-card-header {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 1rem;
        }

        .arch-card-title {
            font-size: 1.1rem;
            font-weight: 700;
            color: #fff;
        }

        .spec-row {
            display: flex;
            justify-content: space-between;
            padding: 0.6rem 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            font-size: 0.9rem;
        }

        .spec-label { color: var(--text-secondary); }
        .spec-val { font-weight: 600; color: #fff; }

        /* Modal Overlay */
        .modal-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(8px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
            animation: fadeIn 0.2s ease-out;
        }

        .modal-overlay.active {
            display: flex;
        }

        .modal-card {
            background: var(--bg-surface);
            border: 1px solid var(--border-hover);
            border-radius: 18px;
            width: 100%;
            max-width: 480px;
            padding: 2rem;
            box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.7);
        }

        .modal-title {
            font-size: 1.3rem;
            font-weight: 700;
            color: #fff;
            margin-bottom: 0.5rem;
        }

        .modal-subtitle {
            font-size: 0.85rem;
            color: var(--text-secondary);
            margin-bottom: 1.5rem;
        }

        .form-group {
            margin-bottom: 1.25rem;
        }

        .form-label {
            display: block;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-secondary);
            margin-bottom: 0.5rem;
        }

        .form-control {
            width: 100%;
            padding: 0.75rem 1rem;
            background: var(--bg-surface-elevated);
            border: 1px solid var(--border);
            border-radius: 10px;
            color: #fff;
            font-size: 0.95rem;
            outline: none;
        }

        .form-control:focus {
            border-color: var(--primary);
            box-shadow: 0 0 0 3px var(--primary-glow);
        }

        .modal-actions {
            display: flex;
            justify-content: flex-end;
            gap: 0.75rem;
            margin-top: 1.75rem;
        }

        .btn-secondary {
            background: var(--bg-surface-elevated);
            color: var(--text-secondary);
            border: 1px solid var(--border);
            padding: 0.65rem 1.25rem;
            border-radius: 10px;
            font-weight: 600;
            cursor: pointer;
        }

        .empty-state {
            text-align: center;
            padding: 3rem 1rem;
            color: var(--text-tertiary);
            font-size: 0.95rem;
        }

        @media (max-width: 900px) {
            .kpi-grid { grid-template-columns: repeat(2, 1fr); }
            .arch-grid { grid-template-columns: 1fr; }
            header { flex-direction: column; gap: 1rem; align-items: flex-start; }
        }
    </style>
</head>
<body>
    <div class="dashboard-container">
        <!-- Top Executive Header -->
        <header>
            <div class="brand-area">
                <div class="brand-logo">🩺</div>
                <div class="brand-text">
                    <h1>Healthcare Clinic Operations</h1>
                    <p>Dr. Umang Chourasia • Gunjan AI Voice Receptionist</p>
                </div>
            </div>
            <div class="header-actions">
                <div class="live-status-pill">
                    <span class="pulse-indicator"></span>
                    <span>AI Receptionist Live</span>
                </div>
                <button class="btn-new-appointment" onclick="openBookModal()">
                    <span>+</span> Book Appointment
                </button>
            </div>
        </header>

        <!-- Executive KPI Metrics -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="kpi-header">
                    <span class="kpi-title">Active Bookings</span>
                    <span class="kpi-icon">📅</span>
                </div>
                <div class="kpi-value" id="kpi-total-booked">0</div>
                <div class="kpi-subtext">
                    <span id="kpi-today-count" style="color: var(--cyan); font-weight: 600;">0 today</span> • 
                    <span id="kpi-tomorrow-count" style="color: #93c5fd; font-weight: 600;">0 tomorrow</span>
                </div>
            </div>

            <div class="kpi-card kpi-emerald">
                <div class="kpi-header">
                    <span class="kpi-title">Projected Revenue</span>
                    <span class="kpi-icon">💰</span>
                </div>
                <div class="kpi-value" id="kpi-revenue">₹0</div>
                <div class="kpi-subtext">₹500 consultation fee per booked patient</div>
            </div>

            <div class="kpi-card kpi-cyan">
                <div class="kpi-header">
                    <span class="kpi-title">Total Voice Calls</span>
                    <span class="kpi-icon">📞</span>
                </div>
                <div class="kpi-value" id="kpi-total-calls">0</div>
                <div class="kpi-subtext">Tata Smartflo Telephony Inbound</div>
            </div>

            <div class="kpi-card kpi-purple">
                <div class="kpi-header">
                    <span class="kpi-title">Booking Conversion</span>
                    <span class="kpi-icon">⚡</span>
                </div>
                <div class="kpi-value" id="kpi-conversion">0%</div>
                <div class="kpi-subtext">Calls leading to confirmed bookings</div>
            </div>
        </div>

        <!-- Navigation Tabs -->
        <div class="tab-nav">
            <button class="tab-btn active" onclick="switchTab('tab-appointments', event)">📅 Appointments</button>
            <button class="tab-btn" onclick="switchTab('tab-slots', event)">🗂️ Slot Availability Matrix</button>
            <button class="tab-btn" onclick="switchTab('tab-calls', event)">📞 Call Transcripts</button>
            <button class="tab-btn" onclick="switchTab('tab-system', event)">⚙️ AI System Status</button>
        </div>

        <!-- Tab 1: Appointments Management -->
        <div id="tab-appointments" class="tab-pane active">
            <div class="content-card">
                <div class="table-controls">
                    <div class="search-bar">
                        <svg class="search-icon-svg" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"></path>
                        </svg>
                        <input type="text" id="searchInput" placeholder="Search by patient name, phone, date..." oninput="renderAppointments()">
                    </div>
                    <div class="filter-group">
                        <button class="filter-chip active" onclick="setFilter('all', event)">All</button>
                        <button class="filter-chip" onclick="setFilter('today', event)">Today</button>
                        <button class="filter-chip" onclick="setFilter('tomorrow', event)">Tomorrow</button>
                        <button class="filter-chip" onclick="setFilter('active', event)">Booked</button>
                        <button class="filter-chip" onclick="setFilter('cancelled', event)">Cancelled</button>
                    </div>
                </div>

                <div class="table-wrapper">
                    <table>
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Patient Name</th>
                                <th>Contact Number</th>
                                <th>Appointment Date</th>
                                <th>Time Slot</th>
                                <th>Status</th>
                                <th>Action</th>
                            </tr>
                        </thead>
                        <tbody id="appointmentTableBody">
                            <tr><td colspan="7" class="empty-state">Loading real-time clinic appointments...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Tab 2: Slot Availability Matrix -->
        <div id="tab-slots" class="tab-pane">
            <div class="content-card">
                <div class="matrix-header">
                    <div>
                        <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff;">Daily Slot Capacity Matrix</h2>
                        <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 2px;">Visual 30-minute consultation slot schedule for Dr. Umang Chourasia</p>
                    </div>
                    <div>
                        <input type="date" id="matrixDateInput" class="date-picker-input" onchange="fetchSlotMatrix()">
                    </div>
                </div>
                <div id="slotGrid" class="slot-grid-container">
                    <!-- Dynamic slots injected here -->
                </div>
            </div>
        </div>

        <!-- Tab 3: Call Transcripts & Logs -->
        <div id="tab-calls" class="tab-pane">
            <div class="content-card">
                <div style="margin-bottom: 1.5rem;">
                    <h2 style="font-size: 1.25rem; font-weight: 700; color: #fff;">Live Call Logs & Transcripts</h2>
                    <p style="font-size: 0.85rem; color: var(--text-secondary); margin-top: 2px;">Complete conversation records captured by Gunjan AI</p>
                </div>
                <div id="callLogsContainer">
                    <div class="empty-state">Loading call session recordings...</div>
                </div>
            </div>
        </div>

        <!-- Tab 4: AI System Architecture & Status -->
        <div id="tab-system" class="tab-pane">
            <div class="arch-grid">
                <div class="arch-card">
                    <div class="arch-card-header">
                        <span style="font-size: 1.5rem;">🧠</span>
                        <div class="arch-card-title">AI Voice Pipeline</div>
                    </div>
                    <div class="spec-row"><span class="spec-label">STT Model</span><span class="spec-val" style="color: var(--cyan);">Cartesia Ink-Whisper (16kHz Multilingual)</span></div>
                    <div class="spec-row"><span class="spec-label">LLM Intelligence</span><span class="spec-val" style="color: var(--purple);">Groq LPU (openai/gpt-oss-20b)</span></div>
                    <div class="spec-row"><span class="spec-label">TTS Voice Synthesis</span><span class="spec-val" style="color: var(--emerald);">Cartesia Sonic-Turbo (0.85x pacing)</span></div>
                    <div class="spec-row"><span class="spec-label">Voice Latency Target</span><span class="spec-val" style="color: #93c5fd;">Sub-800ms End-to-End</span></div>
                    <div class="spec-row"><span class="spec-label">Pre-Cached Greeting</span><span class="spec-val" style="color: var(--emerald);">0ms Instant Stream</span></div>
                </div>

                <div class="arch-card">
                    <div class="arch-card-header">
                        <span style="font-size: 1.5rem;">🏥</span>
                        <div class="arch-card-title">Clinic Configuration</div>
                    </div>
                    <div class="spec-row"><span class="spec-label">Clinic Name</span><span class="spec-val">Healthcare Clinic</span></div>
                    <div class="spec-row"><span class="spec-label">Attending Doctor</span><span class="spec-val">Dr. Umang Chourasia</span></div>
                    <div class="spec-row"><span class="spec-label">Consultation Fee</span><span class="spec-val">₹500 (30 Mins)</span></div>
                    <div class="spec-row"><span class="spec-label">Working Hours</span><span class="spec-val">Mon - Sat (9:00 AM - 5:00 PM)</span></div>
                    <div class="spec-row"><span class="spec-label">Database</span><span class="spec-val" style="color: var(--emerald);">PostgreSQL Connection Pool</span></div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal for Manual Appointment Booking -->
    <div id="bookModal" class="modal-overlay">
        <div class="modal-card">
            <div class="modal-title">Book New Appointment</div>
            <div class="modal-subtitle">Manually register an in-clinic appointment with Dr. Umang Chourasia</div>
            
            <form onsubmit="handleManualBooking(event)">
                <div class="form-group">
                    <label class="form-label">Patient Full Name</label>
                    <input type="text" id="modalPatientName" class="form-control" placeholder="e.g. Rahul Sharma" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Mobile Number (10 Digits)</label>
                    <input type="tel" id="modalPatientPhone" class="form-control" placeholder="e.g. 9876543210" pattern="[0-9]{10}" required>
                </div>
                <div class="form-group">
                    <label class="form-label">Appointment Date</label>
                    <input type="date" id="modalApptDate" class="form-control" required onchange="loadModalSlots()">
                </div>
                <div class="form-group">
                    <label class="form-label">Available Time Slot</label>
                    <select id="modalApptTime" class="form-control" required>
                        <option value="">Select date first...</option>
                    </select>
                </div>
                <div class="modal-actions">
                    <button type="button" class="btn-secondary" onclick="closeBookModal()">Cancel</button>
                    <button type="submit" class="btn-new-appointment">Confirm Booking</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        let allAppointments = [];
        let currentFilter = 'all';

        function switchTab(tabId, ev) {
            document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            if (ev) ev.currentTarget.classList.add('active');

            if (tabId === 'tab-slots') {
                fetchSlotMatrix();
            } else if (tabId === 'tab-calls') {
                fetchCallLogs();
            }
        }

        async function fetchAppointments() {
            try {
                const res = await fetch('/api/appointments');
                allAppointments = await res.json();
                updateKPIs();
                renderAppointments();
            } catch (err) {
                console.error("Failed to fetch appointments:", err);
            }
        }

        function updateKPIs() {
            const todayStr = new Date().toISOString().split('T')[0];
            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            const tomorrowStr = tomorrow.toISOString().split('T')[0];

            const booked = allAppointments.filter(a => a.status === 'BOOKED');
            const todayCount = booked.filter(a => a.date === todayStr).length;
            const tomorrowCount = booked.filter(a => a.date === tomorrowStr).length;

            document.getElementById('kpi-total-booked').textContent = booked.length;
            document.getElementById('kpi-today-count').textContent = `${todayCount} today`;
            document.getElementById('kpi-tomorrow-count').textContent = `${tomorrowCount} tomorrow`;
            document.getElementById('kpi-revenue').textContent = `₹${(booked.length * 500).toLocaleString()}`;
        }

        function setFilter(f, ev) {
            currentFilter = f;
            document.querySelectorAll('.filter-chip').forEach(b => b.classList.remove('active'));
            ev.currentTarget.classList.add('active');
            renderAppointments();
        }

        function renderAppointments() {
            const query = (document.getElementById('searchInput').value || '').toLowerCase();
            const todayStr = new Date().toISOString().split('T')[0];
            const tomorrow = new Date();
            tomorrow.setDate(tomorrow.getDate() + 1);
            const tomorrowStr = tomorrow.toISOString().split('T')[0];

            let filtered = allAppointments.filter(a => {
                if (currentFilter === 'today' && (a.date !== todayStr || a.status !== 'BOOKED')) return false;
                if (currentFilter === 'tomorrow' && (a.date !== tomorrowStr || a.status !== 'BOOKED')) return false;
                if (currentFilter === 'active' && a.status !== 'BOOKED') return false;
                if (currentFilter === 'cancelled' && a.status !== 'CANCELLED') return false;

                if (query) {
                    const matchName = (a.name || '').toLowerCase().includes(query);
                    const matchPhone = (a.phone || '').toLowerCase().includes(query);
                    const matchDate = (a.date || '').toLowerCase().includes(query);
                    const matchTime = (a.time || '').toLowerCase().includes(query);
                    return matchName || matchPhone || matchDate || matchTime;
                }
                return true;
            });

            const tbody = document.getElementById('appointmentTableBody');
            if (filtered.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No appointments found matching your search.</td></tr>';
                return;
            }

            tbody.innerHTML = filtered.map(a => {
                let badgeClass = 'badge-cancelled';
                let statusText = '✕ Cancelled';
                if (a.status === 'BOOKED') {
                    badgeClass = 'badge-booked';
                    statusText = '● Confirmed Booked';
                } else if (a.status === 'COMPLETED') {
                    badgeClass = 'badge-completed';
                    statusText = '✓ Completed';
                }

                return `
                <tr>
                    <td style="color: var(--text-tertiary); font-weight: 600; font-family: monospace;">#${a.id}</td>
                    <td>
                        <div class="patient-cell">
                            <div class="patient-avatar">${(a.name || 'P')[0].toUpperCase()}</div>
                            <div class="patient-name-text">${escapeHtml(a.name || 'Anonymous')}</div>
                        </div>
                    </td>
                    <td><span class="phone-tag">${escapeHtml(formatPhone(a.phone))}</span></td>
                    <td><strong style="color: #fff;">${a.date}</strong></td>
                    <td><span class="time-pill">🕒 ${formatTime(a.time)}</span></td>
                    <td>
                        <span class="badge-status ${badgeClass}">
                            ${statusText}
                        </span>
                    </td>
                    <td>
                        ${a.status === 'BOOKED' ? `
                            <button class="btn-cancel-action" onclick="cancelAppointment(${a.id}, '${escapeHtml(a.name)}')">Cancel</button>
                        ` : '<span style="color: var(--text-tertiary); font-size: 0.85rem;">None</span>'}
                    </td>
                </tr>
                `;
            }).join('');
        }

        async function fetchSlotMatrix() {
            const dateInput = document.getElementById('matrixDateInput');
            if (!dateInput.value) {
                dateInput.value = new Date().toISOString().split('T')[0];
            }
            const targetDate = dateInput.value;

            try {
                const res = await fetch(`/api/slots?date=${targetDate}`);
                const data = await res.json();
                const container = document.getElementById('slotGrid');
                
                const allDaySlots = [
                    '09:00', '09:30', '10:00', '10:30', '11:00', '11:30',
                    '13:00', '13:30', '14:00', '14:30', '15:00', '15:30', '16:00', '16:30'
                ];

                const availSet = new Set(data.available_slots || []);

                container.innerHTML = allDaySlots.map(t => {
                    const isAvail = availSet.has(t);
                    return `
                        <div class="slot-box ${isAvail ? 'available' : 'booked'}" onclick="${isAvail ? `openBookModalWithSlot('${targetDate}', '${t}')` : ''}">
                            <div class="slot-time">${formatTime(t)}</div>
                            <div class="slot-status-text">${isAvail ? '✓ Available' : 'Booked'}</div>
                        </div>
                    `;
                }).join('');
            } catch (err) {
                console.error("Failed to load slot matrix:", err);
            }
        }

        async function fetchCallLogs() {
            try {
                const res = await fetch('/api/calls');
                const data = await res.json();
                const container = document.getElementById('callLogsContainer');
                
                document.getElementById('kpi-total-calls').textContent = data.length;
                const bookedCount = allAppointments.filter(a => a.status === 'BOOKED' || a.status === 'COMPLETED').length;
                const conversion = data.length > 0 ? Math.round((bookedCount / data.length) * 100) : 0;
                document.getElementById('kpi-conversion').textContent = `${conversion}%`;

                if (data.length === 0) {
                    container.innerHTML = '<div class="empty-state">No call recordings yet. Inbound calls via Tata Smartflo will appear here automatically.</div>';
                    return;
                }

                container.innerHTML = data.map(c => {
                    let parsedJson = null;
                    let turns = [];
                    try {
                        if (c.transcript && c.transcript.trim().startsWith('{')) {
                            parsedJson = JSON.parse(c.transcript);
                            turns = parsedJson.turns || [];
                        }
                    } catch (e) {
                        parsedJson = null;
                    }

                    const jsonStrEscaped = escapeHtml(c.transcript || '{}');
                    const rawJsonPretty = parsedJson ? JSON.stringify(parsedJson, null, 2) : c.transcript;

                    return `
                        <div class="call-card" id="call-card-${c.session_id}">
                            <div class="call-card-header">
                                <div class="call-info-left">
                                    <div class="call-icon">📞</div>
                                    <div>
                                        <div class="call-phone">${escapeHtml(formatPhone(c.caller_phone || 'Anonymous'))}</div>
                                        <div class="call-time-meta">${c.started_at.replace('T', ' ').slice(0, 19)} • Duration: ${c.duration_seconds || 0}s • Session: #${c.session_id.slice(0, 8)}</div>
                                    </div>
                                </div>
                                <div style="display: flex; align-items: center; gap: 0.5rem;">
                                    <button class="btn-secondary" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;" onclick="copyJsonTranscript('${c.session_id}')">📋 Copy JSON</button>
                                    <button class="btn-secondary" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;" onclick="downloadJsonTranscript('${c.session_id}')">💾 Download JSON</button>
                                    <button class="btn-secondary" style="padding: 0.4rem 0.8rem; font-size: 0.8rem;" onclick="toggleRawJson('${c.session_id}')">{ } Toggle JSON</button>
                                    <span class="badge-status badge-booked">COMPLETED</span>
                                </div>
                            </div>
                            
                            <!-- Chat Bubbles View -->
                            <div id="chat-view-${c.session_id}" class="call-transcript-drawer" style="display: flex; flex-direction: column; gap: 0.6rem;">
                                ${turns.length > 0 ? turns.map(t => `
                                    <div style="display: flex; gap: 0.6rem; align-items: flex-start; ${t.role === 'assistant' ? 'align-self: flex-start;' : 'align-self: flex-end; flex-direction: row-reverse;'}">
                                        <div style="width: 28px; height: 28px; border-radius: 8px; background: ${t.role === 'assistant' ? 'rgba(6, 182, 212, 0.2)' : 'rgba(59, 130, 246, 0.2)'}; display: flex; align-items: center; justify-content: center; font-size: 0.85rem; font-weight: 700; color: ${t.role === 'assistant' ? 'var(--cyan)' : 'var(--primary)'};">
                                            ${t.role === 'assistant' ? '🩺' : '👤'}
                                        </div>
                                        <div style="max-width: 80%; background: ${t.role === 'assistant' ? 'rgba(15, 23, 42, 0.8)' : 'rgba(30, 41, 59, 0.9)'}; border: 1px solid ${t.role === 'assistant' ? 'rgba(6, 182, 212, 0.3)' : 'rgba(59, 130, 246, 0.3)'}; border-radius: 10px; padding: 0.6rem 0.9rem;">
                                            <div style="font-size: 0.75rem; font-weight: 700; color: ${t.role === 'assistant' ? 'var(--cyan)' : '#93c5fd'}; margin-bottom: 2px;">${escapeHtml(t.speaker)}</div>
                                            <div style="color: #f1f5f9; font-size: 0.9rem; line-height: 1.4;">${escapeHtml(t.text)}</div>
                                        </div>
                                    </div>
                                `).join('') : `
                                    <div style="color: #cbd5e1; white-space: pre-wrap;">${escapeHtml(c.transcript || 'No transcript recorded.')}</div>
                                `}
                            </div>

                            <!-- Raw JSON View (Hidden by Default) -->
                            <div id="json-view-${c.session_id}" class="call-transcript-drawer" style="display: none; background: #070b14; font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; color: #38bdf8;">
                                <pre id="json-text-${c.session_id}" style="white-space: pre-wrap; word-break: break-all;">${escapeHtml(rawJsonPretty)}</pre>
                            </div>
                        </div>
                    `;
                }).join('');
            } catch (err) {
                console.error("Failed to fetch calls:", err);
            }
        }

        function toggleRawJson(sessionId) {
            const chatView = document.getElementById(`chat-view-${sessionId}`);
            const jsonView = document.getElementById(`json-view-${sessionId}`);
            if (chatView && jsonView) {
                const isJson = jsonView.style.display !== 'none';
                jsonView.style.display = isJson ? 'none' : 'block';
                chatView.style.display = isJson ? 'flex' : 'none';
            }
        }

        function copyJsonTranscript(sessionId) {
            const jsonPre = document.getElementById(`json-text-${sessionId}`);
            if (jsonPre) {
                navigator.clipboard.writeText(jsonPre.textContent).then(() => {
                    alert(`Full call transcript JSON copied to clipboard! (Session #${sessionId.slice(0, 8)})`);
                }).catch(() => {
                    alert("Failed to copy to clipboard.");
                });
            }
        }

        function downloadJsonTranscript(sessionId) {
            const jsonPre = document.getElementById(`json-text-${sessionId}`);
            if (jsonPre) {
                const blob = new Blob([jsonPre.textContent], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `call_transcript_${sessionId}.json`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            }
        }

        function formatPhone(p) {
            if (!p) return '-';
            const digits = p.replace(/\\D/g, '');
            if (digits.length === 10) return digits.replace(/(\\d{5})(\\d{5})/, '$1 $2');
            return p;
        }

        function formatTime(t) {
            if (!t) return '-';
            const parts = t.split(':');
            let h = parseInt(parts[0]);
            const m = parts[1];
            const ampm = h >= 12 ? 'PM' : 'AM';
            h = h % 12 || 12;
            return `${h}:${m} ${ampm}`;
        }

        function escapeHtml(str) {
            return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        async function cancelAppointment(id, name) {
            if (!confirm(`Cancel appointment #${id} for ${name}?`)) return;
            try {
                const res = await fetch(`/api/cancel/${id}`, { method: 'POST' });
                const data = await res.json();
                if (data.success) {
                    fetchAppointments();
                } else {
                    alert(data.message || 'Failed to cancel appointment');
                }
            } catch (err) {
                alert('Network error while cancelling');
            }
        }

        function openBookModal() {
            document.getElementById('modalApptDate').value = new Date().toISOString().split('T')[0];
            loadModalSlots();
            document.getElementById('bookModal').classList.add('active');
        }

        function openBookModalWithSlot(d, t) {
            document.getElementById('modalApptDate').value = d;
            loadModalSlots().then(() => {
                document.getElementById('modalApptTime').value = t;
            });
            document.getElementById('bookModal').classList.add('active');
        }

        function closeBookModal() {
            document.getElementById('bookModal').classList.remove('active');
        }

        async function loadModalSlots() {
            const dateVal = document.getElementById('modalApptDate').value;
            if (!dateVal) return;
            const select = document.getElementById('modalApptTime');
            select.innerHTML = '<option value="">Loading slots...</option>';

            try {
                const res = await fetch(`/api/slots?date=${dateVal}`);
                const data = await res.json();
                if (data.available_slots && data.available_slots.length > 0) {
                    select.innerHTML = data.available_slots.map(s => `
                        <option value="${s}">${formatTime(s)}</option>
                    `).join('');
                } else {
                    select.innerHTML = '<option value="">No slots available on this date</option>';
                }
            } catch (err) {
                select.innerHTML = '<option value="">Error loading slots</option>';
            }
        }

        async function handleManualBooking(e) {
            e.preventDefault();
            const name = document.getElementById('modalPatientName').value.trim();
            const phone = document.getElementById('modalPatientPhone').value.trim();
            const dateVal = document.getElementById('modalApptDate').value;
            const timeVal = document.getElementById('modalApptTime').value;

            try {
                const res = await fetch('/api/appointments/book', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ patient_name: name, patient_phone: phone, appointment_date: dateVal, appointment_time: timeVal })
                });
                const data = await res.json();
                if (data.success) {
                    closeBookModal();
                    fetchAppointments();
                    alert(`Appointment booked successfully for ${name}!`);
                } else {
                    alert(data.message || 'Failed to book appointment');
                }
            } catch (err) {
                alert('Network error while booking appointment');
            }
        }

        // Initialize dashboard
        fetchAppointments();
        fetchCallLogs();
        setInterval(fetchAppointments, 3000);
        setInterval(fetchCallLogs, 5000);
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
    
    now = datetime.now()
    data = []
    for r in rows:
        aid, name, phone, dt, tm, status = r
        time_str = tm.strftime("%H:%M") if hasattr(tm, "strftime") else str(tm)[:5]
        
        # Dynamically treat past booked slots as COMPLETED
        if status == 'BOOKED':
            try:
                appt_datetime = datetime.combine(dt, tm)
                if appt_datetime < now:
                    status = 'COMPLETED'
            except Exception:
                pass

        data.append({
            "id": aid,
            "name": name,
            "phone": phone,
            "date": str(dt),
            "time": time_str,
            "status": status
        })
    return web.json_response(data)

async def book_appointment_api(request):
    try:
        payload = await request.json()
        name = payload.get("patient_name")
        phone = payload.get("patient_phone")
        dt = payload.get("appointment_date")
        tm = payload.get("appointment_time")
        result = appointments_db.book_appointment(name, phone, dt, tm)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)}, status=400)

async def get_slots_api(request):
    target_date = request.query.get("date", datetime.now().strftime("%Y-%m-%d"))
    slots = appointments_db.get_available_slots(target_date)
    return web.json_response({"target_date": target_date, "available_slots": slots, "count": len(slots)})

async def get_calls_api(request):
    calls = appointments_db.get_call_sessions(limit=50)
    return web.json_response(calls)

async def get_call_json_api(request):
    session_id = request.match_info['session_id']
    calls = appointments_db.get_call_sessions(limit=100)
    match = next((c for c in calls if c['session_id'] == session_id), None)
    if match:
        try:
            parsed = json.loads(match['transcript']) if match['transcript'] else match
            return web.json_response(parsed)
        except Exception:
            return web.json_response({"session_id": session_id, "raw_transcript": match['transcript']})
    return web.json_response({"error": "Call session not found"}, status=404)

async def cancel_api(request):
    appt_id = int(request.match_info['id'])
    with appointments_db.get_db_cursor() as cur:
        cur.execute("UPDATE appointments SET status = 'CANCELLED' WHERE id = %s RETURNING id, patient_name", (appt_id,))
        row = cur.fetchone()
    if row:
        return web.json_response({"success": True, "message": f"Appointment #{appt_id} cancelled"})
    return web.json_response({"success": False, "message": "Appointment not found"}, status=404)

async def main():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/api/appointments', get_appointments_api)
    app.router.add_post('/api/appointments/book', book_appointment_api)
    app.router.add_get('/api/slots', get_slots_api)
    app.router.add_get('/api/calls', get_calls_api)
    app.router.add_get('/api/calls/{session_id}/json', get_call_json_api)
    app.router.add_post('/api/cancel/{id}', cancel_api)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 5000)
    print("Healthcare Clinic Live Production Dashboard running on http://localhost:5000")
    await site.start()
    await asyncio.Future()

if __name__ == '__main__':
    asyncio.run(main())
