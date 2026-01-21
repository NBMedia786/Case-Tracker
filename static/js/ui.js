
let selectedCases = new Set();

function renderCases(cases) {
    const tbody = document.getElementById('cases-body');
    const loading = document.getElementById('loading-indicator');
    const empty = document.getElementById('empty-state');
    const table = document.getElementById('cases-table');

    tbody.innerHTML = '';

    if (loading) loading.style.display = 'none';

    if (!cases || cases.length === 0) {
        if (table) table.style.display = 'none';
        if (empty) empty.style.display = 'block';
        return;
    } else {
        if (table) table.style.display = 'table';
        if (empty) empty.style.display = 'none';
    }

    selectedCases.clear();
    const masterCheckbox = document.getElementById('master-checkbox');
    if (masterCheckbox) masterCheckbox.checked = false;
    updateScheduleButton();

    cases.forEach(c => {
        tbody.appendChild(createCaseRow(c));
    });

    cases.forEach(c => {
        if (c.processing_status === 'processing' || c.processing_status === 'queued') {
            resumeProgressWrapper(c.id);
        }
    });
}

function createCaseRow(c) {
    const row = document.createElement('tr');
    row.id = `row-${c.id}`; // Add ID for easy access

    const status = (c.status || 'Open').toLowerCase();
    let badgeClass = 'badge-open';
    if (status === 'closed') badgeClass = 'badge-closed';
    if (status === 'verdict reached') badgeClass = 'badge-verdict';

    const isClosed = status === 'closed' || status === 'verdict reached';
    const statusBadge = `<span class="status-badge ${badgeClass}">${escapeHtml(c.status || 'Open')}</span>`;

    const lastDateHtml = (c.last_hearing_date && c.last_hearing_date !== 'Unknown')
        ? `<span class="date-past">${c.last_hearing_date}</span>`
        : `<span style="color: var(--text-muted); opacity: 0.5;">--</span>`;

    let nextDateHtml;

    if (isClosed) {
        nextDateHtml = '<span style="color: var(--text-muted); font-style: italic; font-size: 13px;">Case Closed</span>';
    } else {
        const rawNext = c.next_hearing_date && c.next_hearing_date !== 'Unknown' ? c.next_hearing_date : null;

        if (rawNext) {
            nextDateHtml = `<span class="date-future">${rawNext}</span>`;
        } else {
            nextDateHtml = '<span style="color: var(--text-muted)">Unknown</span>';
        }

        if ((c.confidence || 'high').toLowerCase() === 'low') {
            nextDateHtml += ` <span style="cursor:help" title="AI Confidence: LOW. Please verify manually.">⚠️</span>`;
        }
    }

    const metaInfo = [];
    if (c.victim_name) metaInfo.push(`Victim: ${escapeHtml(c.victim_name)}`);
    if (c.suspect_name) metaInfo.push(`Suspect: ${escapeHtml(c.suspect_name)}`);
    const metaHtml = metaInfo.length > 0 ? metaInfo.join(' • ') : '';

    const linkIcon = c.docket_url ? `<span title="Official Docket URL Available" style="color: var(--accent-blue); font-size: 14px;"> 🔗</span>` : '';


    row.innerHTML = `
        <td style="text-align:center;">
            <input type="checkbox" class="case-checkbox" value="${c.id}" onchange="toggleCaseSelection(${c.id}, this)" style="cursor:pointer; transform: scale(1.1);">
        </td>
        
        <td>
            <div class="case-info">
                <a href="#" class="case-title-link" onclick="openModal(${c.id}); return false;">
                    ${escapeHtml(c.case_name)} ${linkIcon}
                </a>
                <div class="case-meta">
                    ${metaHtml}
                </div>
            </div>
        </td>
        
        <td>${lastDateHtml}</td>
        <td>${nextDateHtml}</td>
        
        <td>${statusBadge}</td>
        
        <td class="action-cell">
            <div style="display:flex; gap: 8px;">
                <button onclick="triggerUpdateWrapper(${c.id}, this)" class="action-icon-btn" title="Run AI Research">
                   🔄
                </button>
                 <button onclick="window.deleteCase(${c.id})" class="action-icon-btn btn-delete-icon" title="Delete Case">
                   🗑️
                </button>
            </div>
        </td>
    `;
    return row;
}

function resumeProgressWrapper(id) {
    const row = document.getElementById(`row-${id}`);
    if (!row) return;

    const actionCell = row.querySelector('.action-cell');
    if (!actionCell) return;

    renderProgressUI(actionCell, id, 0, "Resuming...");
    startProgressPolling(id, actionCell);
}

async function triggerUpdateWrapper(id, btnElement) {
    if (btnElement.disabled) return;
    btnElement.disabled = true;

    const parentTd = btnElement.closest('td');

    renderProgressUI(parentTd, id, 0, "Starting...");

    try {
        if (typeof window.triggerResearch === 'function') {
            await window.triggerResearch(id);
        } else if (typeof triggerResearch === 'function') {
            await triggerResearch(id);
        } else {
            await fetch(`/api/trigger_update/${id}`, { method: 'POST' });
        }

        startProgressPolling(id, parentTd);

    } catch (e) {
        console.error(e);
        parentTd.innerHTML = `<span style="color:red; font-size:12px;">Error</span>`;
        setTimeout(() => window.syncWithServer(), 2000);
    }
}

function renderProgressUI(container, id, percent, message) {
    const progressId = `progress-${id}`;
    container.innerHTML = `
        <div class="progress-container" style="width: 120px; text-align: center;">
            <div style="background-color: #e2e8f0; border-radius: 4px; height: 6px; width: 100%; overflow: hidden; margin-bottom: 4px;">
                <div id="${progressId}-bar" style="background-color: var(--accent-blue); height: 100%; width: ${percent}%; transition: width 0.5s ease;"></div>
            </div>
            <div id="${progressId}-text" style="font-size: 10px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${message}</div>
        </div>
    `;
}

function startProgressPolling(id, container) {
    const progressId = `progress-${id}`;

    const intervalId = setInterval(async () => {
        try {
            let progress;
            if (typeof window.getCaseProgress === 'function') {
                progress = await window.getCaseProgress(id);
            } else if (typeof getCaseProgress === 'function') {
                progress = await getCaseProgress(id);
            } else {
                const res = await fetch(`/api/progress/${id}`);
                progress = await res.json();
            }

            const bar = document.getElementById(`${progressId}-bar`);
            const text = document.getElementById(`${progressId}-text`);

            if (bar && text) {
                const percent = progress.percent || 0;
                bar.style.width = `${percent}%`;
                text.innerText = progress.message ? `(${percent}%) ${progress.message}` : `${percent}%`;

                if (percent >= 100 || progress.status === 'complete') {
                    clearInterval(intervalId);
                    text.innerText = "Done!";
                    setTimeout(async () => {
                        await window.syncWithServer();
                    }, 1000);
                }
            } else {
                clearInterval(intervalId);
            }

        } catch (e) {
            console.error("Progress poll error", e);
        }
    }, 1000);
}


function toggleAddCaseForm() {
    const modal = document.getElementById('addCaseModal');
    const formBody = document.getElementById('addCaseFormBody');

    if (modal.style.display === 'block') {
        modal.style.display = 'none';
    } else {
        formBody.innerHTML = '';
        formBody.appendChild(createProfessionalForm());
        modal.style.display = 'block';
    }
}

function createProfessionalForm() {
    const formContainer = document.createElement('div');

    function createInputGroup(label, id, type = 'text', placeholder = '') {
        const group = document.createElement('div');
        group.className = 'form-group';

        const labelEl = document.createElement('label');
        labelEl.className = 'form-label';
        labelEl.htmlFor = id;
        labelEl.innerText = label;

        const inputEl = document.createElement('input');
        inputEl.type = type;
        inputEl.id = id;
        inputEl.className = 'form-input';
        inputEl.placeholder = placeholder;

        group.appendChild(labelEl);
        group.appendChild(inputEl);
        return group;
    }

    formContainer.appendChild(createInputGroup('Case Name *', 'case-name', 'text', 'e.g., State vs. John Doe'));
    formContainer.appendChild(createInputGroup('Official Docket URL (Optional)', 'docket-url', 'url', 'https://court-portal.gov/...'));
    formContainer.appendChild(createInputGroup('Next Hearing Date (Optional)', 'next-hearing-date', 'date'));
    formContainer.appendChild(createInputGroup('Victim Name (Optional)', 'victim-name'));
    formContainer.appendChild(createInputGroup('Suspect Name (Optional)', 'suspect-name'));

    const btnGroup = document.createElement('div');
    btnGroup.style.textAlign = 'right';
    btnGroup.style.marginTop = '30px';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-outline';
    cancelBtn.innerText = 'Cancel';
    cancelBtn.style.marginRight = '10px';
    cancelBtn.onclick = toggleAddCaseForm;

    const saveBtn = document.createElement('button');
    saveBtn.className = 'btn btn-primary';
    saveBtn.innerText = 'Save Case';
    saveBtn.onclick = async () => {
        const nameInput = document.getElementById('case-name');
        if (!nameInput.value.trim()) {
            alert("Case Name is required.");
            nameInput.focus();
            return;
        }

        saveBtn.innerText = "Saving...";
        saveBtn.disabled = true;

        const formData = {
            case_name: nameInput.value.trim(),
            docket_url: document.getElementById('docket-url').value.trim() || null,
            next_hearing_date: document.getElementById('next-hearing-date').value || null,
            victim_name: document.getElementById('victim-name').value.trim() || null,
            suspect_name: document.getElementById('suspect-name').value.trim() || null,
        };

        try {
            await window.addCase(formData);
            toggleAddCaseForm();
        } catch (error) {
            console.error("Failed to add case:", error);
            alert("Error adding case: " + error.message);
        } finally {
            saveBtn.innerText = "Save Case";
            saveBtn.disabled = false;
        }
    };

    btnGroup.appendChild(cancelBtn);
    btnGroup.appendChild(saveBtn);
    formContainer.appendChild(btnGroup);

    return formContainer;
}


function toggleCaseSelection(id, checkbox) {
    if (checkbox.checked) selectedCases.add(id);
    else selectedCases.delete(id);
    updateScheduleButton();
}

function toggleAll(masterCheckbox) {
    const checkboxes = document.querySelectorAll('.case-checkbox');
    checkboxes.forEach(cb => {
        cb.checked = masterCheckbox.checked;
        const id = parseInt(cb.value);
        if (masterCheckbox.checked) selectedCases.add(id);
        else selectedCases.delete(id);
    });
    updateScheduleButton();
}

function updateScheduleButton() {
    const btn = document.getElementById('btn-schedule');
    if (selectedCases.size > 0) {
        btn.style.display = 'inline-flex';
        btn.innerHTML = `⏰ Schedule (${selectedCases.size})`;
    } else {
        btn.style.display = 'none';
    }
}

function openScheduleModal() {
    const modal = document.getElementById('scheduleModal');
    const timeInput = document.getElementById('schedule-time');

    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    tomorrow.setHours(9, 0, 0, 0);
    const offset = tomorrow.getTimezoneOffset() * 60000;
    const localISOTime = new Date(tomorrow.getTime() - offset).toISOString().slice(0, 16);

    timeInput.value = localISOTime;
    modal.style.display = 'block';
}

async function confirmSchedule() {
    const timeVal = document.getElementById('schedule-time').value;
    if (!timeVal) return alert("Please pick a date and time.");

    const btn = document.querySelector('#scheduleModal .btn-primary');
    const originalText = btn.innerText;
    btn.innerText = "Scheduling...";
    btn.disabled = true;

    try {
        if (typeof window.scheduleCustomCheck !== 'function') {
            const response = await fetch('/api/schedule_custom_check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    case_ids: Array.from(selectedCases),
                    run_time: timeVal
                })
            });
            const result = await response.json();
            if (result.success) handleScheduleSuccess();
            else alert("Error: " + result.error);
        } else {
            const result = await window.scheduleCustomCheck(Array.from(selectedCases), timeVal);
            if (result.success) handleScheduleSuccess();
            else alert("Error: " + result.error);
        }
    } catch (e) {
        console.error(e);
        alert("Failed to schedule.");
    } finally {
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

function handleScheduleSuccess() {
    alert("Schedule Set Successfully!");
    document.getElementById('scheduleModal').style.display = 'none';
    document.getElementById('master-checkbox').checked = false;
    document.querySelectorAll('.case-checkbox').forEach(cb => cb.checked = false);
    selectedCases.clear();
    updateScheduleButton();
}

function escapeHtml(unsafe) {
    if (unsafe === null || unsafe === undefined) return '';
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function openModal(caseId) {
    const casesList = window.cases || [];
    const caseData = casesList.find(c => c.id === caseId);

    if (caseData) {
        document.getElementById('modalTitle').innerText = caseData.case_name;
        const notesContent = caseData.notes ? caseData.notes : "No summary available yet. Run AI research to generate notes.";
        document.getElementById('modalBody').innerText = notesContent;
        document.getElementById('summaryModal').style.display = "block";
    }
}

function closeModal() {
    document.getElementById('summaryModal').style.display = "none";
}

window.onclick = function (event) {
    if (event.target.classList.contains('modal')) {
        event.target.style.display = "none";
    }
}
