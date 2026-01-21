/**
 * Legal Case Tracker - Main Application
 * Entry point that ties together API and UI components.
 */

window.cases = []; // Expose globally for UI helper access if needed
let isLoading = false;


/**
 * Sync cases from server and update UI.
 */
async function syncWithServer() {
    if (isLoading) return;
    isLoading = true;

    const tbody = document.getElementById('cases-body');
    if (tbody && window.cases.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 20px;">Loading cases...</td></tr>';
    }

    try {
        const casesData = await getCases(); // From api.js
        window.cases = casesData;

        if (window.renderCases) {
            window.renderCases(window.cases);
        } else {
            console.error("renderCases not found in window scope");
        }

    } catch (error) {
        console.error('Sync error:', error);
        if (tbody) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:red; padding: 20px;">Failed to load cases: ${error.message} <button onclick="window.syncWithServer()">Retry</button></td></tr>`;
        }
    } finally {
        isLoading = false;
    }
}

/**
 * Handle adding a new case.
 * Called by UI form submission.
 */
async function addCaseWrapper(caseData) {
    const result = await apiAddCase(caseData);
    if (window.showToast) window.showToast('Case added successfully!', 'success');

    await syncWithServer();

    return result;
}

/**
 * Handle research trigger for a case.
 */
async function triggerUpdate(caseId) {
    if (window.showToast) window.showToast('Starting research...', 'info');

    try {
        const result = await triggerResearch(caseId); // From api.js
        if (window.showToast) window.showToast('Research completed!', 'success');
        await syncWithServer();
    } catch (error) {
        console.error('Research error:', error);
        if (window.showToast) window.showToast(`Error: ${error.message}`, 'error');
    }
}

/**
 * Handle case deletion.
 */
async function deleteCaseWrapper(caseId) {
    if (!confirm('Are you sure you want to delete this case?')) {
        return;
    }

    try {
        await apiDeleteCase(caseId); // From api.js
        if (window.showToast) window.showToast('Case deleted', 'success');
        await syncWithServer();
    } catch (error) {
        console.error('Delete error:', error);
        if (window.showToast) window.showToast(`Error: ${error.message}`, 'error');
    }
}

window.syncWithServer = syncWithServer;
window.triggerUpdate = triggerUpdate;
window.deleteCase = deleteCaseWrapper;
window.addCase = addCaseWrapper; // For UI to call
window.scheduleCustomCheck = scheduleCustomCheck; // Direct exposure from api.js namespace
window.getCaseProgress = getCaseProgress;


function initApp() {
    syncWithServer();
}

document.addEventListener('DOMContentLoaded', initApp);
