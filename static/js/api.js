const API_BASE_URL = '/api';

/**
 * Fetch all cases from the server.
 * @param {string} status - Optional status filter
 * @returns {Promise<Array>} Array of case objects
 */
async function getCases(status = null) {
    const url = status ? `${API_BASE_URL}/cases?status=${status}` : `${API_BASE_URL}/cases`;

    const response = await fetch(url);
    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Failed to fetch cases');
    }

    return result.data;
}

/**
 * Get a single case by ID.
 * @param {number} caseId - The case ID
 * @returns {Promise<Object>} Case object
 */
async function getCaseById(caseId) {
    const response = await fetch(`${API_BASE_URL}/cases/${caseId}`);
    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Case not found');
    }

    return result.data;
}

/**
 * Add a new case to the database.
 * @param {Object} caseData - Case data object
 * @param {string} caseData.case_name - Required case name
 * @param {string} caseData.victim_name - Optional victim name
 * @param {string} caseData.suspect_name - Optional suspect name
 * @param {string} caseData.next_hearing_date - Optional date (YYYY-MM-DD)
 * @param {string} caseData.notes - Optional notes
 * @returns {Promise<Object>} Created case object
 */
async function apiAddCase(caseData) {
    const response = await fetch(`${API_BASE_URL}/add_case`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(caseData)
    });

    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Failed to add case');
    }

    return result.data;
}

/**
 * Update an existing case.
 * @param {number} caseId - The case ID
 * @param {Object} updateData - Fields to update
 * @returns {Promise<Object>} Updated case object
 */
async function updateCase(caseId, updateData) {
    const response = await fetch(`${API_BASE_URL}/cases/${caseId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updateData)
    });

    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Failed to update case');
    }

    return result.data;
}

/**
 * Delete a case by ID.
 * @param {number} caseId - The case ID
 * @returns {Promise<boolean>} True if successful
 */
async function apiDeleteCase(caseId) {
    const response = await fetch(`${API_BASE_URL}/cases/${caseId}`, {
        method: 'DELETE'
    });

    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Failed to delete case');
    }

    return true;
}

/**
 * Trigger research agent for a specific case.
 * @param {number} caseId - The case ID
 * @returns {Promise<Object>} Research result
 */
async function triggerResearch(caseId) {
    const response = await fetch(`${API_BASE_URL}/trigger_update/${caseId}`, {
        method: 'POST'
    });

    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Research failed');
    }

    return result;
}

/**
 * Trigger research for all eligible cases.
 * @returns {Promise<Object>} Result
 */
async function triggerAllResearch() {
    const response = await fetch(`${API_BASE_URL}/trigger_all`, {
        method: 'POST'
    });

    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Batch research failed');
    }

    return result;
}

/**
 * Get scheduler status.
 * @returns {Promise<Object>} Scheduler status
 */
async function getSchedulerStatus() {
    const response = await fetch(`${API_BASE_URL}/scheduler/status`);
    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Failed to get scheduler status');
    }

    return result;
}

/**
 * Schedule custom check for multiple cases.
 * @param {Array} caseIds - List of case IDs
 * @param {string} runTime - ISO datetime string
 * @returns {Promise<Object>} Result
 */
async function scheduleCustomCheck(caseIds, runTime) {
    const response = await fetch(`${API_BASE_URL}/schedule_custom_check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ case_ids: caseIds, run_time: runTime })
    });

    const result = await response.json();

    if (!response.ok || !result.success) {
        throw new Error(result.error || 'Scheduling failed');
    }

    return result;
}

/**
 * Get the real-time progress for a case.
 * @param {number} caseId - The case ID
 * @returns {Promise<Object>} Progress object { step, percent, message, status }
 */
async function getCaseProgress(caseId) {
    const response = await fetch(`${API_BASE_URL}/progress/${caseId}`);
    const result = await response.json();
    return result;
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        getCases,
        getCaseById,
        addCase,
        updateCase,
        deleteCase: apiDeleteCase,
        triggerResearch,
        triggerAllResearch,
        getSchedulerStatus,
        scheduleCustomCheck,
        getCaseProgress,
        API_BASE_URL
    };
}
