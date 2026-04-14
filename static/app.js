/* ════════════════════════════════════════════════════════
   CamAI Dashboard — app.js
   Multi-block single-screen surveillance dashboard
════════════════════════════════════════════════════════ */


// ── Utility: API fetch ─────────────────────────────────────────────────────
async function api(url, { method = 'GET', body } = {}) {
    const opts = {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        ...(body ? { body: JSON.stringify(body) } : {}),
    };
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}


// ── Utility: Toast ─────────────────────────────────────────────────────────
function showToast(message, type = 'success') {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    
    let icon = '';
    if (type === 'success') {
        icon = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
    } else if (type === 'error') {
        icon = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>`;
    } else if (type === 'loading') {
        icon = `<svg class="spinner" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83M2 12h4m12 0h4M4.93 19.07l2.83-2.83m8.48-8.48l2.83-2.83"/></svg>`;
    }

    toast.innerHTML = `${icon}<span>${message}</span>`;
    container.appendChild(toast);

    if (type !== 'loading') {
        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }
    return toast; // Return toast element so it can be removed manually
}


// ── Clock ──────────────────────────────────────────────────────────────────
function initClock() {
    const el = document.getElementById('liveClock');
    if (!el) return;
    const update = () => {
        const now = new Date();
        const d = String(now.getDate()).padStart(2, '0');
        const m = String(now.getMonth() + 1).padStart(2, '0');
        const y = now.getFullYear();
        const h = String(now.getHours()).padStart(2, '0');
        const i = String(now.getMinutes()).padStart(2, '0');
        const s = String(now.getSeconds()).padStart(2, '0');
        el.textContent = `${d}/${m}/${y} — ${h}:${i}:${s}`;
    };
    update();
    setInterval(update, 1000);
}


// ── Alert State ────────────────────────────────────────────────────────────
const alertState = {
    count: 0,
    lastAlertTime: null,
    lastAlertStr: '—',
};

function updateAlertSummary(data) {
    alertState.count++;
    alertState.lastAlertTime = new Date();
    alertState.lastAlertStr = data.time ? data.time.split(' ')[1] || data.time : '—';

    // Update Block 4: Summary
    const sumToday = document.getElementById('sumAlertsToday');
    if (sumToday) sumToday.textContent = alertState.count;

    const sumStatus = document.getElementById('sumStatus');
    if (sumStatus) {
        if (data.intrusion) {
            sumStatus.textContent = 'Xâm nhập!';
            sumStatus.className = 'sum-status danger';
        } else {
            sumStatus.textContent = 'Phát hiện người';
            sumStatus.className = 'sum-status warning';
        }
        // Reset to safe after 10s if no new alert
        clearTimeout(alertState._resetTimer);
        alertState._resetTimer = setTimeout(() => {
            if (sumStatus) {
                sumStatus.textContent = 'An toàn';
                sumStatus.className = 'sum-status safe';
            }
        }, 10000);
    }

    const sumLast = document.getElementById('sumLastAlert');
    if (sumLast) sumLast.textContent = alertState.lastAlertStr;

    // Update overlay zone status if intrusion
    if (data.intrusion) {
        setZoneStatus('intrusion');
        setTimeout(() => setZoneStatus('safe'), 8000);
    }

    // Update overlay last event
    const ovLastEvent = document.getElementById('ovLastEvent');
    if (ovLastEvent) {
        ovLastEvent.innerHTML = `
            <svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            <span>Sự kiện lúc ${alertState.lastAlertStr}</span>`;
    }
}

function setZoneStatus(status) {
    const el = document.getElementById('ovZoneStatus');
    const txt = document.getElementById('zoneStatusText');
    if (!el || !txt) return;
    el.className = `ov-stat ov-zone-status ${status}`;
    const labels = { safe: 'Vùng: An toàn', warning: 'Vùng: Cảnh báo', intrusion: 'Vùng: Xâm nhập!' };
    txt.textContent = labels[status] || labels.safe;
}


// ── Alert Manager (SSE) ────────────────────────────────────────────────────
class AlertManager {
    constructor() {
        this.list   = document.getElementById('alertList');
        this.badge  = document.getElementById('alertBadge');
        this.empty  = document.getElementById('emptyState');
        this.count  = 0;
        if (this.list) this._connect();
    }

    _connect() {
        const es = new EventSource('/api/alerts');
        es.onmessage = e => {
            const data = JSON.parse(e.data);
            if (data.type === 'status') {
                this._updateRealtimeStats(data);
            } else {
                this._add(data);
            }
        };
        es.onerror = () => setTimeout(() => this._connect(), 3000);
    }

    _updateRealtimeStats(data) {
        // Update People Count
        const pCount = document.getElementById('peopleCount');
        if (pCount) pCount.textContent = `${data.count} người`;

        // Update Zone Status Overlay (Real-time)
        if (data.intrusion) {
            setZoneStatus('intrusion');
        } else {
            // Only set to safe if there's no active alert override
            // (setZoneStatus might be called by alerts with a longer hold)
            // But for high-frequency status, we want it to follow the AI accurately
            setZoneStatus('safe');
        }

        // Update AI status pill
        const aiStatus = document.getElementById('headerAI');
        if (aiStatus) {
            aiStatus.textContent = data.count > 0 ? "YOLOv8 · Detecting" : "YOLOv8 · Monitoring";
            aiStatus.className = `stat-pill ${data.count > 0 ? 'amber' : 'green'}`;
        }
    }

    _add(data) {
        this.count++;

        // Update badge
        if (this.badge) this.badge.textContent = this.count;

        // Hide empty state
        if (this.empty) this.empty.style.display = 'none';

        // Update summary panel
        updateAlertSummary(data);

        // Build alert item
        const el = document.createElement('div');
        el.className = 'alert-item' + (data.intrusion ? ' alert-intrusion' : '');

        const iconSvg = data.intrusion
            ? `<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/></svg>`
            : `<svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>`;

        const timeStr = data.time ? (data.time.split(' ')[1] || data.time) : '';
        const label = data.msg || (data.intrusion ? 'Cảnh báo xâm nhập' : 'Phát hiện người');

        el.innerHTML = `
            <div class="ai-icon">${iconSvg}</div>
            <div class="ai-content">
                <div class="ai-title">${label}</div>
                <div class="ai-meta">
                    <span class="ai-time">${data.time || ''}</span>
                    <span class="ai-cam">· CAM-01</span>
                    <span class="ai-tg-sent">✓ Telegram</span>
                </div>
            </div>`;

        el.addEventListener('click', () => {
            if (window.currentAlertsPopup) {
                window.currentAlertsPopup.open();
            }
        });

        if (this.list) this.list.prepend(el);
    }
}


// ── Zone Editor ────────────────────────────────────────────────────────────
class ZoneEditor {
    static MAX_POINTS = 4;

    constructor(canvas) {
        this.canvas   = canvas;
        this.ctx      = canvas.getContext('2d');
        this.points   = [];
        this.dragging = null;
        this.editMode = false;
        this.POINT_R  = 7;
        this.DRAG_D   = 16;
        this._bindCanvas();
        this._loadFromServer();
    }

    enterEdit() {
        this.editMode = true;
        this.points   = [];
        this.canvas.classList.add('interactive');
        this._draw();
        this._updateBtns();
    }

    exitEdit() {
        this.editMode = false;
        this.dragging = null;
        this.canvas.classList.remove('interactive');
        this._updateBtns();
    }

    _bindCanvas() {
        const c = this.canvas;
        c.addEventListener('click', e => {
            if (!this.editMode || this.dragging !== null) return;
            if (this.points.length >= ZoneEditor.MAX_POINTS) return;
            this.points.push(this._pos(e));
            this._draw();
            this._updateBtns();
        });
        c.addEventListener('mousedown', e => {
            if (!this.editMode) return;
            const p = this._pos(e);
            const idx = this.points.findIndex(([x, y]) =>
                Math.hypot(x - p[0], y - p[1]) <= this.DRAG_D
            );
            if (idx !== -1) this.dragging = idx;
        });
        c.addEventListener('mousemove', e => {
            if (this.dragging === null) return;
            this.points[this.dragging] = this._pos(e);
            this._draw();
        });
        c.addEventListener('mouseup',    () => { this.dragging = null; });
        c.addEventListener('mouseleave', () => { this.dragging = null; });
    }

    _pos(e) {
        const r = this.canvas.getBoundingClientRect();
        return [
            Math.round((e.clientX - r.left) * 640 / r.width),
            Math.round((e.clientY - r.top)  * 480 / r.height),
        ];
    }

    _draw() {
        const { canvas: cv, ctx, points } = this;
        const r = cv.getBoundingClientRect();
        const W = r.width, H = r.height;
        cv.width = W;
        cv.height = H;
        ctx.clearRect(0, 0, W, H);

        if (!points.length) return;

        const sx = W / 640, sy = H / 480;
        const px = ([x, y]) => [x * sx, y * sy];
        const pts = points.map(px);

        // Draw completed polygon
        if (points.length === ZoneEditor.MAX_POINTS) {
            ctx.beginPath();
            ctx.moveTo(...pts[0]);
            pts.slice(1).forEach(p => ctx.lineTo(...p));
            ctx.closePath();
            ctx.fillStyle   = 'rgba(59,130,246,0.18)';
            ctx.fill();
            ctx.strokeStyle = '#3b82f6';
            ctx.lineWidth   = 2;
            ctx.stroke();

            // Center label
            const cx = pts.reduce((s, p) => s + p[0], 0) / 4;
            const cy = pts.reduce((s, p) => s + p[1], 0) / 4;
            ctx.font      = 'bold 12px Inter, sans-serif';
            ctx.fillStyle = 'rgba(59,130,246,0.85)';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText('FORBIDDEN ZONE', cx, cy);
        }

        // Edit mode extras
        if (this.editMode) {
            if (points.length < ZoneEditor.MAX_POINTS && pts.length > 1) {
                ctx.beginPath();
                ctx.moveTo(...pts[0]);
                pts.slice(1).forEach(p => ctx.lineTo(...p));
                ctx.strokeStyle = '#f97316';
                ctx.lineWidth   = 1.5;
                ctx.setLineDash([6, 4]);
                ctx.stroke();
                ctx.setLineDash([]);
            }

            pts.forEach(([x, y], i) => {
                ctx.beginPath();
                ctx.arc(x, y, this.POINT_R + 4, 0, Math.PI * 2);
                ctx.fillStyle = 'rgba(59,130,246,0.15)';
                ctx.fill();

                ctx.beginPath();
                ctx.arc(x, y, this.POINT_R, 0, Math.PI * 2);
                ctx.fillStyle   = '#3b82f6';
                ctx.fill();
                ctx.strokeStyle = '#fff';
                ctx.lineWidth   = 2;
                ctx.stroke();

                ctx.font         = 'bold 9px Inter';
                ctx.fillStyle    = '#fff';
                ctx.textAlign    = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(i + 1, x, y);
            });
        }
    }

    _updateBtns() {
        const btn  = document.getElementById('btnZoneToggle');
        const txt  = document.getElementById('btnZoneText');
        const done = this.points.length === ZoneEditor.MAX_POINTS;

        if (!btn || !txt) return;

        if (!this.editMode) {
            btn.classList.remove('active', 'btn-save-state');
            txt.textContent = this.points.length > 0 ? 'Edit Zone' : 'Vẽ Zone';
            btn.disabled = false;
        } else {
            btn.classList.add('active');
            if (done) {
                btn.classList.add('btn-save-state');
                txt.textContent = 'Lưu Zone';
                btn.disabled = false;
            } else {
                btn.classList.remove('btn-save-state');
                txt.textContent = `Điểm (${this.points.length}/4)`;
                btn.disabled = true;
            }
        }
    }

    async save() {
        if (this.points.length !== ZoneEditor.MAX_POINTS) return;
        const btn = document.getElementById('btnZoneToggle');
        const txt = document.getElementById('btnZoneText');
        if (btn) btn.disabled = true;
        if (txt) txt.textContent = 'Đang lưu...';
        try {
            const res = await api('/api/zone', { method: 'POST', body: { points: this.points } });
            if (res.status === 'ok') {
                this.exitEdit();
                showToast('Zone đã được lưu!');
                setTimeout(() => this._updateBtns(), 500);
            }
        } catch (err) {
            showToast('Lưu zone thất bại!', 'error');
            this._updateBtns();
        }
    }

    async reset() {
        try {
            await api('/api/zone', { method: 'DELETE' });
        } catch {}
        this.points   = [];
        this.dragging = null;
        this.exitEdit();
        this._draw();
        showToast('Đã xóa vùng cấm');
    }

    async _loadFromServer() {
        try {
            const res = await api('/api/zone');
            if (res.status === 'ok' && Array.isArray(res.points) && res.points.length === ZoneEditor.MAX_POINTS) {
                this.points = res.points;
            }
        } catch {}
        this._draw();
        this._updateBtns();
    }
}


// ── Settings Panel ─────────────────────────────────────────────────────────
async function initSettings() {
    const overlay  = document.getElementById('settingsOverlay');
    const backdrop = document.getElementById('settingsBackdrop');
    const btnSave  = document.getElementById('btnSaveSettings');
    const btnTest  = document.getElementById('btnTestTelegram');
    const btnTest2 = document.getElementById('btnTestTelegram2');

    const iZoneH     = document.getElementById('inpZoneHold');
    const iZoneC     = document.getElementById('inpZoneCool');
    const iTeleToken = document.getElementById('inpTeleToken');
    const iTeleChat  = document.getElementById('inpTeleChat');

    const openSettings  = () => overlay.classList.add('open');
    const closeSettings = () => overlay.classList.remove('open');

    // Multiple open triggers
    ['btnOpenSettings', 'btnOpenSettings2', 'btnOpenSettings3'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.addEventListener('click', openSettings);
    });

    document.getElementById('btnCloseSettings')?.addEventListener('click', closeSettings);
    backdrop?.addEventListener('click', closeSettings);

    // Load config from server
    try {
        const data = await api('/api/config');
        if (data.status === 'ok') {
            if (iZoneH && data.zone_hold_secs)   iZoneH.value      = data.zone_hold_secs;
            if (iZoneC && data.zone_cooldown)     iZoneC.value      = data.zone_cooldown;
            if (iTeleToken && data.telegram_token)  iTeleToken.value = data.telegram_token;
            if (iTeleChat && data.telegram_chat_id) iTeleChat.value  = data.telegram_chat_id;

            // Sync Quick Settings card
            syncQuickSettings(data);
        }
    } catch (e) {
        console.error('Failed to load config', e);
    }

    // Save
    btnSave?.addEventListener('click', async () => {
        const origText = btnSave.innerHTML;
        btnSave.innerHTML = `<span>Đang lưu...</span>`;
        try {
            const body = {
                zone_hold_secs:   parseFloat(Math.max(0.1, iZoneH?.value || 3)),
                zone_cooldown:    parseInt(Math.max(1, iZoneC?.value || 5)),
                telegram_token:   iTeleToken?.value || '',
                telegram_chat_id: iTeleChat?.value || '',
                zone_max_points:  4,
            };
            await api('/api/config', { method: 'POST', body });
            syncQuickSettings(body);
            showToast('Cài đặt đã được lưu!');
            closeSettings();
        } catch {
            showToast('Lưu thất bại!', 'error');
        } finally {
            btnSave.innerHTML = origText;
        }
    });

    // Test Telegram
    const doTestTelegram = async () => {
        const loadingToast = showToast('Đang kết nối để gửi tin nhắn test...', 'loading');
        try {
            const res = await api('/api/test-telegram', { method: 'POST' });
            loadingToast.remove();
            
            if (res.status === 'ok') {
                showToast('✓ Đã gửi tin nhắn! Hãy kiểm tra Telegram.', 'success');
            } else {
                showToast(res.message || 'Kết nối thất bại.', 'error');
            }
        } catch (err) {
            loadingToast.remove();
            showToast('Lỗi: Cổng kết nối server bị gián đoạn.', 'error');
        }
    };

    btnTest?.addEventListener('click', doTestTelegram);
    btnTest2?.addEventListener('click', doTestTelegram);
}

function syncQuickSettings(cfg) {
    const qsZoneHold = document.getElementById('qsZoneHold');
    const qsCooldown = document.getElementById('qsCooldown');
    const qsTelegram = document.getElementById('qsTelegram');

    if (qsZoneHold) qsZoneHold.textContent = `${cfg.zone_hold_secs || 3}s`;
    if (qsCooldown) qsCooldown.textContent  = `${cfg.zone_cooldown || 5}s`;
    if (qsTelegram) {
        const hasTg = cfg.telegram_token && cfg.telegram_token.length > 10;
        qsTelegram.textContent  = hasTg ? 'Bật' : 'Tắt';
        qsTelegram.className    = `qs-val ${hasTg ? 'green' : 'red'}`;
    }
}








// ── Alerts History Popup (for dashboard.html) ────────────────────────────
class AlertsHistoryPopup {
    constructor() {
        this.popup       = document.getElementById('alertsHistoryPopup');
        this.backdrop    = document.getElementById('alertsHistoryBackdrop');
        this.listDiv     = document.getElementById('alertsHistoryList');
        this.detailDiv   = document.getElementById('alertsHistoryDetail');
        this.searchBox   = document.getElementById('alertsSearchBox');
        this.sortBox     = document.getElementById('alertsSortBox');
        this.btnClose    = document.getElementById('btnCloseAlertsHistory');
        
        this.records     = [];
        this.selectedId  = null;
        this.filters     = { search: '', sort: 'newest' };
        
        this._bindEvents();
    }

    _bindEvents() {
        this.backdrop?.addEventListener('click', () => this.close());
        this.btnClose?.addEventListener('click', () => this.close());
        this.searchBox?.addEventListener('input', e => {
            this.filters.search = e.target.value.toLowerCase();
            this._render();
        });
        this.sortBox?.addEventListener('change', e => {
            this.filters.sort = e.target.value;
            this._render();
        });
    }

    open() {
        this.popup?.classList.add('active');
        this._loadRecords();
    }

    close() {
        this.popup?.classList.remove('active');
    }

    async _loadRecords() {
        try {
            const res = await api('/api/history');
            if (res.status === 'ok') {
                this.records = res.data || [];
                this._render();
            }
        } catch (err) {
            console.error('Load history error:', err);
        }
    }

    _render() {
        // Filter records
        let filtered = this.records;
        if (this.filters.search) {
            filtered = filtered.filter(r => 
                r.track_id.toString().includes(this.filters.search) ||
                r.time.includes(this.filters.search)
            );
        }

        // Sort
        if (this.filters.sort === 'oldest') {
            filtered.sort((a, b) => a.timestamp - b.timestamp);
        } else {
            filtered.sort((a, b) => b.timestamp - a.timestamp);
        }

        // Render list with thumbnails
        this.listDiv.innerHTML = filtered.map(r => `
            <div class="alerts-history-item ${this.selectedId === r.id ? 'active' : ''}" 
                 onclick="event.stopPropagation(); window.currentAlertsPopup?.selectRecord('${r.id}')">
                <div class="item-thumb-wrapper">
                    <img src="${r.img_url}" class="item-thumb" onerror="this.style.display='none'">
                </div>
                <div class="item-content">
                    <div class="item-meta">
                        <span class="item-time">${r.time}</span>
                        <span class="item-id">#${r.track_id}</span>
                    </div>
                    <div class="item-type">${r.intrusion ? '🚨 Xâm nhập' : '👤 Phát hiện'}</div>
                </div>
            </div>
        `).join('');

        if (!this.selectedId && filtered.length > 0) {
            this.selectRecord(filtered[0].id);
        }
    }

    selectRecord(recordId) {
        this.selectedId = recordId;
        const record = this.records.find(r => r.id === recordId);
        
        // Update active class in list
        this.listDiv.querySelectorAll('.alerts-history-item').forEach(el => {
            const isTarget = el.getAttribute('onclick')?.includes(recordId);
            el.classList.toggle('active', isTarget);
        });

        // Show rich detail
        if (record) {
            this.detailDiv.innerHTML = `
                <div class="detail-view-container">
                    <div class="detail-image-wrapper">
                        <img src="${record.img_url}" alt="Alert Detail" onerror="this.src='/static/placeholder.jpg'">
                    </div>
                    <div class="detail-info-grid">
                        <div class="detail-info-card">
                            <h4>Thời điểm phát hiện</h4>
                            <p>${record.date} ${record.time}</p>
                        </div>
                        <div class="detail-info-card">
                            <h4>Loại sự kiện</h4>
                            <p>
                                <span class="type-badge ${record.intrusion ? 'intrusion' : 'person'}">
                                    ${record.intrusion ? '🚨 Xâm nhập vùng cấm' : '👤 Phát hiện người'}
                                </span>
                            </p>
                        </div>
                        <div class="detail-info-card">
                            <h4>Định danh đối tượng</h4>
                            <p>Track ID: #${record.track_id}</p>
                        </div>
                    </div>
                </div>
            `;
        } else {
            this.detailDiv.innerHTML = '<div class="alerts-detail-empty">Chọn một sự kiện từ danh sách để xem ảnh chụp chi tiết</div>';
        }
    }
}

// ── History Manager (for history.html) ────────────────────────────────────
class HistoryManager {
    constructor(onCardClick) {
        this.grid    = document.getElementById('historyGrid');
        this.onClick = onCardClick;
        this.records = [];
        this.filters = { search: '', date: '', sort: 'newest' };
        this._bindFilters();
    }

    _bindFilters() {
        const s = document.getElementById('searchHistory');
        const d = document.getElementById('filterDate');
        const o = document.getElementById('sortHistory');

        s?.addEventListener('input', e => { this.filters.search = e.target.value.toLowerCase(); this.applyFilters(); });
        d?.addEventListener('change', e => { this.filters.date = e.target.value; this.applyFilters(); });
        o?.addEventListener('change', e => { this.filters.sort = e.target.value; this.applyFilters(); });
    }

    async load() {
        if (!this.grid) return;
        this.grid.innerHTML = '<p style="color:var(--text-sub);font-size:.85rem;padding:20px;">Đang tải...</p>';
        try {
            const data = await api('/api/history');
            this.records = data.data ?? [];
            this.applyFilters();
        } catch {
            this.grid.innerHTML = '<p style="color:var(--red);padding:20px;">Không thể tải lịch sử.</p>';
        }
    }

    applyFilters() {
        let filtered = [...this.records];
        if (this.filters.search) {
            filtered = filtered.filter(r =>
                r.id.toLowerCase().includes(this.filters.search) ||
                (r.intrusion ? 'zone breach' : 'person detected').includes(this.filters.search)
            );
        }
        if (this.filters.date) filtered = filtered.filter(r => r.date === this.filters.date);
        filtered.sort((a, b) => this.filters.sort === 'newest' ? b.timestamp - a.timestamp : a.timestamp - b.timestamp);
        this._render(filtered);
    }

    _render(records) {
        if (!records.length) {
            this.grid.innerHTML = '<p style="color:var(--text-sub);font-size:.85rem;padding:20px;">Không tìm thấy sự kiện nào.</p>';
            return;
        }
        this.grid.innerHTML = '';
        records.forEach(r => {
            const card = document.createElement('div');
            card.className = 'history-card';
            card.innerHTML = `
                <img src="${r.img_url}" alt="Event" loading="lazy">
                <div class="hc-body">
                    <div class="hc-id" style="color:${r.intrusion ? 'var(--red)' : 'var(--text)'}">
                        ${r.intrusion ? '⚡ Zone Breach' : '👁 Person Detected'}
                    </div>
                    <div class="hc-meta">
                        <span>${r.date}</span>
                        <span>${r.time}</span>
                    </div>
                </div>`;
            card.addEventListener('click', () => this.onClick(r));
            this.grid.appendChild(card);
        });
    }
}


// ── Detail View ────────────────────────────────────────────────────────────
function showDetail(record) {
    const card = document.getElementById('detailCard');
    if (!card) return;
    card.innerHTML = `
        <div class="detail-img-wrap">
            <img src="${record.img_url}" alt="Event">
            <span class="detail-badge">${record.intrusion ? 'Xâm nhập' : 'Phát hiện'}</span>
        </div>
        <div class="detail-body">
            <div class="detail-alert-row">
                <span class="detail-icon">${record.intrusion ? '🚨' : '👁'}</span>
                <div>
                    <div class="detail-event-title">${record.intrusion ? 'Zone Breach' : 'Person Detected'}</div>
                    <div class="detail-event-sub">${record.intrusion ? 'Đối tượng xâm nhập vùng cấm' : 'Người xuất hiện trong khu vực giám sát'}</div>
                </div>
            </div>
            <div class="detail-meta">
                <div class="detail-meta-item">
                    <span class="dm-label">📅 Ngày</span>
                    <span class="dm-value">${record.date || '—'}</span>
                </div>
                <div class="detail-meta-item">
                    <span class="dm-label">🕐 Giờ</span>
                    <span class="dm-value">${record.time || '—'}</span>
                </div>
                <div class="detail-meta-item">
                    <span class="dm-label">⚠️ Trạng thái</span>
                    <span class="dm-value dm-red">Alert Triggered</span>
                </div>
            </div>
        </div>`;
}


// ════════════════════════════════════════════════════════
// BOOTSTRAP
// ════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {

    // ── Always: Clock
    initClock();

    // ── Dashboard (index.html)
    const overlayCanvas = document.getElementById('overlayCanvas');
    if (overlayCanvas) {
        // Zone Editor
        const zoneEditor = new ZoneEditor(overlayCanvas);

        document.getElementById('btnZoneToggle')?.addEventListener('click', () => {
            if (!zoneEditor.editMode) {
                zoneEditor.enterEdit();
            } else if (zoneEditor.points.length === ZoneEditor.MAX_POINTS) {
                zoneEditor.save();
            }
        });

        document.getElementById('btnZoneReset')?.addEventListener('click', () => zoneEditor.reset());

        // Alert SSE
        new AlertManager();

        // Settings
        initSettings();

        // Alerts History Popup
        const alertsHistoryPopup = new AlertsHistoryPopup();
        window.currentAlertsPopup = alertsHistoryPopup;
        document.getElementById('btnOpenAlertsHistory')?.addEventListener('click', () => alertsHistoryPopup.open());
        document.getElementById('btnOpenAlertsHistoryFromLog')?.addEventListener('click', () => alertsHistoryPopup.open());
    }

    // ── History page (history.html)
    const historyGrid = document.getElementById('historyGrid');
    if (historyGrid) {
        const hm = new HistoryManager(record => {
            showDetail(record);
            window.location.hash = 'detailView';
        });
        hm.load();

        const handleHash = () => {
            const hash = window.location.hash;
            const hView = document.getElementById('historyView');
            const dView = document.getElementById('detailView');
            if (hash === '#detailView') {
                hView?.classList.remove('active');
                dView?.classList.add('active');
                const stored = localStorage.getItem('selectedEvent');
                if (stored) {
                    showDetail(JSON.parse(stored));
                    localStorage.removeItem('selectedEvent');
                }
            } else {
                dView?.classList.remove('active');
                hView?.classList.add('active');
            }
        };

        window.addEventListener('hashchange', handleHash);
        handleHash();
    }
});
