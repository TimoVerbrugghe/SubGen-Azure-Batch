/**
 * SubGen-Azure-Batch Web UI Application
 * 
 * Handles file browsing, batch transcription submission, and session monitoring.
 */

class SubGenApp {
    constructor() {
        this.selectedFiles = new Set();
        this.selectedFolders = new Set();
        this.currentPath = '/';
        this.sessions = new Map();
        this.pollInterval = null;
        this.defaultTheme = 'dark';
        this.expandedFolders = new Set();
        this.folderContents = new Map(); // Cache folder contents
        
        this.init();
    }
    
    async init() {
        // Initialize theme
        await this.initTheme();
        
        // Bind event handlers
        this.bindEvents();
        
        // Check configuration
        await this.checkConfig();
        
        // Load initial file list
        await this.loadFiles('/');
        
        // Load existing sessions from server
        await this.loadExistingSessions();
        
        // Start session polling
        this.startPolling();
    }
    
    async loadExistingSessions() {
        try {
            const response = await fetch('/api/batch/sessions');
            if (response.ok) {
                const data = await response.json();
                for (const session of data.sessions) {
                    this.sessions.set(session.session_id, {
                        id: session.session_id,
                        source: session.source || 'ui',
                        total: session.total_jobs,
                        completed: session.completed,
                        failed: session.failed,
                        jobs: session.jobs,
                    });
                }
                if (this.sessions.size > 0) {
                    this.updateSessionsList();
                }
            }
        } catch (error) {
            console.error('Failed to load existing sessions:', error);
        }
    }
    
    async initTheme() {
        // Get default theme from server config, or use stored preference
        try {
            const response = await fetch('/api/config');
            const config = await response.json();
            this.defaultTheme = config.default_theme || 'dark';
        } catch (e) {
            // Ignore - use default
        }
        
        // Check localStorage first, then use default
        const storedTheme = localStorage.getItem('subgen-theme');
        const theme = storedTheme || this.defaultTheme;
        this.setTheme(theme);
    }
    
    setTheme(theme) {
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem('subgen-theme', theme);
        
        // Update toggle icon
        const toggle = document.getElementById('theme-toggle');
        if (toggle) {
            const icon = toggle.querySelector('.theme-icon');
            icon.textContent = theme === 'dark' ? '🌙' : '☀️';
        }
    }
    
    toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        const newTheme = current === 'dark' ? 'light' : 'dark';
        this.setTheme(newTheme);
    }
    
    bindEvents() {
        // Theme toggle
        document.getElementById('theme-toggle')?.addEventListener('click', () => {
            this.toggleTheme();
        });
        
        // Refresh button
        document.getElementById('btn-refresh')?.addEventListener('click', () => {
            this.loadFiles(this.currentPath);
        });
        
        // Select all button
        document.getElementById('btn-select-all')?.addEventListener('click', () => {
            this.selectAllFiles();
        });
        
        // Clear selection button
        document.getElementById('btn-clear')?.addEventListener('click', () => {
            this.clearSelection();
        });
        
        // Transcribe button
        document.getElementById('btn-transcribe')?.addEventListener('click', () => {
            this.startTranscription();
        });
        
        // Clear completed sessions
        document.getElementById('btn-clear-completed')?.addEventListener('click', () => {
            this.clearCompletedSessions();
        });
        
        // Test notification button
        document.getElementById('btn-test-notification')?.addEventListener('click', () => {
            this.sendTestNotification();
        });
    }
    
    async checkConfig() {
        try {
            // Check service status (speech + storage)
            const statusResponse = await fetch('/api/status');
            const status = await statusResponse.json();
            
            // Update Speech status badge
            const speechBadge = document.getElementById('speech-status');
            if (speechBadge) {
                if (!status.speech.configured) {
                    speechBadge.className = 'status-badge warning';
                    speechBadge.title = 'Azure Speech: Not configured';
                } else if (status.speech.connected) {
                    speechBadge.className = 'status-badge ok';
                    speechBadge.title = 'Azure Speech: Connected';
                } else {
                    speechBadge.className = 'status-badge error';
                    speechBadge.title = `Azure Speech: ${status.speech.error || 'Connection failed'}`;
                }
            }
            
            // Update Storage status badge
            const storageBadge = document.getElementById('storage-status');
            if (storageBadge) {
                if (!status.storage.configured) {
                    storageBadge.className = 'status-badge warning';
                    storageBadge.title = 'Azure Storage: Not configured';
                } else if (status.storage.connected) {
                    storageBadge.className = 'status-badge ok';
                    storageBadge.title = 'Azure Storage: Connected';
                } else {
                    storageBadge.className = 'status-badge error';
                    storageBadge.title = `Azure Storage: ${status.storage.error || 'Connection failed'}`;
                }
            }
            
            // Get config for integrations
            const configResponse = await fetch('/api/config');
            const config = await configResponse.json();
            
            // Update footer config status
            const configStatus = document.getElementById('config-status');
            const integrations = [];
            if (config.bazarr_configured) integrations.push('Bazarr');
            if (config.plex_configured) integrations.push('Plex');
            if (config.jellyfin_configured) integrations.push('Jellyfin');
            if (config.emby_configured) integrations.push('Emby');
            
            if (integrations.length > 0) {
                configStatus.textContent = `Integrations: ${integrations.join(', ')}`;
            } else {
                configStatus.textContent = 'No integrations configured';
            }
            
            // Update Bazarr checkbox visibility
            const bazarrCheckbox = document.getElementById('notify-bazarr');
            if (bazarrCheckbox) {
                bazarrCheckbox.parentElement.style.display = config.bazarr_configured ? 'block' : 'none';
            }
            
            // Check notification configuration
            await this.checkNotificationConfig();
            
        } catch (error) {
            console.error('Failed to check config:', error);
            const speechBadge = document.getElementById('speech-status');
            const storageBadge = document.getElementById('storage-status');
            const notificationBadge = document.getElementById('notification-status');
            if (speechBadge) {
                speechBadge.className = 'status-badge error';
                speechBadge.title = 'Connection Error';
            }
            if (storageBadge) {
                storageBadge.className = 'status-badge error';
                storageBadge.title = 'Connection Error';
            }
            if (notificationBadge) {
                notificationBadge.className = 'status-badge warning';
                notificationBadge.title = 'Notifications: Unknown';
            }
        }
    }
    
    async checkNotificationConfig() {
        const notificationBadge = document.getElementById('notification-status');
        const testButton = document.getElementById('btn-test-notification');
        
        try {
            const response = await fetch('/api/notifications/config');
            const config = await response.json();
            
            if (notificationBadge) {
                if (config.pushover_configured) {
                    notificationBadge.className = 'status-badge ok';
                    notificationBadge.title = 'Pushover: Configured';
                    // Show test button when notifications are configured
                    if (testButton) {
                        testButton.style.display = 'inline-block';
                    }
                } else {
                    notificationBadge.className = 'status-badge warning';
                    notificationBadge.title = 'Pushover: Not configured';
                    if (testButton) {
                        testButton.style.display = 'none';
                    }
                }
            }
        } catch (error) {
            console.error('Failed to check notification config:', error);
            if (notificationBadge) {
                notificationBadge.className = 'status-badge warning';
                notificationBadge.title = 'Notifications: Unknown';
            }
            if (testButton) {
                testButton.style.display = 'none';
            }
        }
    }
    
    async sendTestNotification() {
        const testButton = document.getElementById('btn-test-notification');
        const originalText = testButton?.textContent;
        
        try {
            if (testButton) {
                testButton.disabled = true;
                testButton.textContent = 'Sending...';
            }
            
            const response = await fetch('/api/notifications/test', {
                method: 'POST'
            });
            const result = await response.json();
            const pushover = result.results?.pushover;

            if (pushover?.success) {
                this.showToast('Test notification sent successfully!', 'success');
            } else if (pushover?.error) {
                this.showToast(`Notification failed: ${pushover.error}`, 'error');
            } else if (!pushover?.configured) {
                this.showToast('Pushover is not configured', 'warning');
            } else {
                this.showToast('Failed to send test notification', 'error');
            }
        } catch (error) {
            console.error('Failed to send test notification:', error);
            this.showToast(`Error: ${error.message}`, 'error');
        } finally {
            if (testButton) {
                testButton.disabled = false;
                testButton.textContent = originalText;
            }
        }
    }
    
    async loadFiles(path) {
        const fileList = document.getElementById('file-list');
        
        // If loading root, clear and show loading
        if (path === '/') {
            fileList.innerHTML = '<div class="loading-spinner">Loading...</div>';
            this.expandedFolders.clear();
            this.folderContents.clear();
        }
        
        try {
            const response = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            this.currentPath = data.path;
            
            // Cache the folder contents
            this.folderContents.set(path === '/' ? '/' : data.path, data.items);
            
            // Update breadcrumb
            this.updateBreadcrumb(data.path, data.parent);
            
            // Render tree view from root
            this.renderTreeView();
            
        } catch (error) {
            console.error('Failed to load files:', error);
            fileList.innerHTML = `<div class="error-message">Failed to load files: ${error.message}</div>`;
        }
    }
    
    async toggleFolder(path) {
        if (this.expandedFolders.has(path)) {
            // Collapse folder
            this.expandedFolders.delete(path);
            this.renderTreeView();
        } else {
            // Expand folder - load contents if not cached
            if (!this.folderContents.has(path)) {
                // Show loading spinner on the toggle
                const folderItem = document.querySelector(`.file-item-dir[data-path="${CSS.escape(path)}"]`);
                const toggle = folderItem?.querySelector('.folder-toggle');
                if (toggle) {
                    toggle.dataset.originalContent = toggle.textContent;
                    toggle.innerHTML = '<span class="spinner-small"></span>';
                }
                
                try {
                    const response = await fetch(`/api/files?path=${encodeURIComponent(path)}`);
                    if (response.ok) {
                        const data = await response.json();
                        this.folderContents.set(path, data.items);
                        
                        // If this folder (or a parent) is selected, mark newly loaded files as selected
                        if (this.isFolderSelected(path)) {
                            this.selectCachedFilesInFolder(path);
                        }
                    }
                } catch (error) {
                    console.error('Failed to load folder:', error);
                    // Restore toggle on error
                    if (toggle && toggle.dataset.originalContent) {
                        toggle.textContent = toggle.dataset.originalContent;
                    }
                    return;
                }
            }
            this.expandedFolders.add(path);
            this.renderTreeView();
        }
    }
    
    renderTreeView() {
        const fileList = document.getElementById('file-list');
        const rootItems = this.folderContents.get('/') || [];
        
        if (rootItems.length === 0) {
            fileList.innerHTML = '<div class="empty-state">No media folders configured</div>';
            return;
        }
        
        let html = this.renderTreeItems(rootItems, 0);
        fileList.innerHTML = html;
        this.bindTreeEvents();
        
        // Update selection states (handles tri-state checkboxes)
        this.updateSelectionUI();
    }
    
    renderTreeItems(items, depth) {
        let html = '';
        const indent = depth * 20;
        
        items.forEach(item => {
            if (item.is_dir) {
                const isExpanded = this.expandedFolders.has(item.path);
                const isSelected = this.selectedFolders.has(item.path);
                const expandIcon = isExpanded ? '▼' : '▶';
                
                html += `
                    <div class="file-item file-item-dir ${isSelected ? 'selected' : ''}" data-path="${item.path}" style="padding-left: ${12 + indent}px">
                        <span class="folder-toggle" data-path="${item.path}">${expandIcon}</span>
                        <input type="checkbox" class="file-checkbox folder-checkbox" ${isSelected ? 'checked' : ''} data-path="${item.path}">
                        <span class="file-icon">📁</span>
                        <span class="file-name">${item.name}</span>
                    </div>
                `;
                
                // Render children if expanded
                if (isExpanded && this.folderContents.has(item.path)) {
                    const children = this.folderContents.get(item.path);
                    html += this.renderTreeItems(children, depth + 1);
                }
            } else {
                const isSelected = this.selectedFiles.has(item.path);
                const hasSubtitle = item.has_subtitle ? 'has-subtitle' : '';
                const icon = item.has_subtitle ? '✅' : '🎬';
                const size = this.formatSize(item.size);
                
                html += `
                    <div class="file-item file-item-file ${isSelected ? 'selected' : ''} ${hasSubtitle}" data-path="${item.path}" style="padding-left: ${12 + indent + 24}px">
                        <input type="checkbox" class="file-checkbox" ${isSelected ? 'checked' : ''}>
                        <span class="file-icon">${icon}</span>
                        <span class="file-name" title="${item.name}">${item.name}</span>
                        <span class="file-size">${size}</span>
                    </div>
                `;
            }
        });
        
        return html;
    }
    
    bindTreeEvents() {
        const fileList = document.getElementById('file-list');
        
        // Folder toggle (expand/collapse)
        fileList.querySelectorAll('.folder-toggle').forEach(toggle => {
            toggle.addEventListener('click', (e) => {
                e.stopPropagation();
                this.toggleFolder(toggle.dataset.path);
            });
        });
        
        // Folder selection (now synchronous - no API calls needed)
        fileList.querySelectorAll('.file-item-dir').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.classList.contains('folder-toggle') || 
                    e.target.classList.contains('file-checkbox')) return;
                this.toggleFolderSelection(item.dataset.path);
            });
            
            const checkbox = item.querySelector('.folder-checkbox');
            if (checkbox) {
                checkbox.addEventListener('change', (e) => {
                    e.stopPropagation();
                    this.toggleFolderSelection(item.dataset.path, e.target.checked);
                });
            }
        });
        
        // File selection
        fileList.querySelectorAll('.file-item-file').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.classList.contains('file-checkbox')) return;
                this.toggleFileSelection(item.dataset.path);
            });
            
            item.querySelector('.file-checkbox')?.addEventListener('change', (e) => {
                this.toggleFileSelection(item.dataset.path, e.target.checked);
            });
        });
    }
    
    updateBreadcrumb(path, parent) {
        const breadcrumb = document.getElementById('breadcrumb');
        breadcrumb.innerHTML = '<span class="breadcrumb-item active">Media Folders</span>';
    }
    
    toggleFolderSelection(path, forceState = null) {
        const isCurrentlySelected = this.isFolderSelected(path);
        const shouldSelect = forceState === true || (forceState === null && !isCurrentlySelected);
        
        if (shouldSelect) {
            // Just add this folder - backend will expand it
            this.selectedFolders.add(path);
            // Remove any child folders from explicit selection (parent covers them)
            this.removeChildFolderSelections(path);
            // Also select any visible files in cached contents (for UI feedback)
            this.selectCachedFilesInFolder(path);
        } else {
            this.selectedFolders.delete(path);
            // Deselect cached files in this folder
            this.deselectCachedFilesInFolder(path);
        }
        
        this.updateSelectionUI();
    }
    
    isFolderSelected(folderPath) {
        // A folder is selected if it's explicitly selected OR a parent is selected
        if (this.selectedFolders.has(folderPath)) return true;
        
        // Check if any parent is selected
        for (const selected of this.selectedFolders) {
            if (folderPath.startsWith(selected + '/')) return true;
        }
        return false;
    }
    
    isFileInheritedSelected(filePath) {
        // A file inherits selection if any parent folder is selected
        for (const selected of this.selectedFolders) {
            if (filePath.startsWith(selected + '/')) return true;
        }
        return false;
    }
    
    removeChildFolderSelections(parentPath) {
        // Remove any explicitly selected child folders (parent selection covers them)
        for (const folder of Array.from(this.selectedFolders)) {
            if (folder !== parentPath && folder.startsWith(parentPath + '/')) {
                this.selectedFolders.delete(folder);
            }
        }
        // Also remove explicitly selected files under this folder
        for (const file of Array.from(this.selectedFiles)) {
            if (file.startsWith(parentPath + '/')) {
                this.selectedFiles.delete(file);
            }
        }
    }
    
    selectCachedFilesInFolder(folderPath) {
        // Only select files that are already cached (visible in UI)
        const items = this.folderContents.get(folderPath) || [];
        for (const item of items) {
            if (item.is_dir) {
                // Recursively for cached subfolders only
                if (this.folderContents.has(item.path)) {
                    this.selectCachedFilesInFolder(item.path);
                }
            } else {
                this.selectedFiles.add(item.path);
            }
        }
    }
    
    deselectCachedFilesInFolder(folderPath) {
        // Only deselect files that are cached
        const items = this.folderContents.get(folderPath) || [];
        for (const item of items) {
            if (item.is_dir) {
                this.selectedFolders.delete(item.path);
                if (this.folderContents.has(item.path)) {
                    this.deselectCachedFilesInFolder(item.path);
                }
            } else {
                this.selectedFiles.delete(item.path);
            }
        }
    }
    
    getFolderSelectionState(folderPath) {
        // Returns: 'none', 'some', 'all'
        
        // If this folder or a parent is explicitly selected, it's 'all'
        if (this.isFolderSelected(folderPath)) return 'all';
        
        // Check cached contents for partial selection
        const items = this.folderContents.get(folderPath) || [];
        if (items.length === 0) return 'none';
        
        let hasSelected = false;
        let hasUnselected = false;
        
        for (const item of items) {
            if (item.is_dir) {
                const subState = this.getFolderSelectionState(item.path);
                if (subState === 'all') hasSelected = true;
                else if (subState === 'some') { hasSelected = true; hasUnselected = true; }
                else hasUnselected = true;
            } else {
                if (this.selectedFiles.has(item.path) || this.isFileInheritedSelected(item.path)) {
                    hasSelected = true;
                } else {
                    hasUnselected = true;
                }
            }
        }
        
        if (hasSelected && hasUnselected) return 'some';
        if (hasSelected) return 'all';
        return 'none';
    }
    
    toggleFileSelection(path, forceState = null) {
        if (forceState === true || (forceState === null && !this.selectedFiles.has(path))) {
            this.selectedFiles.add(path);
        } else if (forceState === false || (forceState === null && this.selectedFiles.has(path))) {
            this.selectedFiles.delete(path);
        }
        
        // Update parent folder selection states
        this.updateParentFolderStates();
        this.updateSelectionUI();
    }
    
    updateParentFolderStates() {
        // When individual files are selected/deselected, we don't auto-select folders
        // This avoids the complexity of determining "all files selected" 
        // Folders are only selected when explicitly clicked
        // Just trigger UI update - the getFolderSelectionState handles partial states
    }
    
    selectAllFiles() {
        const skipExisting = document.getElementById('skip-existing')?.checked ?? true;
        
        document.querySelectorAll('.file-item-file').forEach(item => {
            const path = item.dataset.path;
            const hasSubtitle = item.classList.contains('has-subtitle');
            
            if (skipExisting && hasSubtitle) return;
            
            this.selectedFiles.add(path);
        });
        
        this.updateParentFolderStates();
        this.updateSelectionUI();
    }
    
    clearSelection() {
        this.selectedFiles.clear();
        this.selectedFolders.clear();
        this.updateSelectionUI();
    }
    
    updateSelectionUI() {
        // Update file items
        document.querySelectorAll('.file-item-file').forEach(item => {
            const selected = this.selectedFiles.has(item.dataset.path);
            item.classList.toggle('selected', selected);
            const checkbox = item.querySelector('.file-checkbox');
            if (checkbox) checkbox.checked = selected;
        });
        
        // Update folder items with tri-state (none, some, all)
        document.querySelectorAll('.file-item-dir').forEach(item => {
            const folderPath = item.dataset.path;
            const state = this.getFolderSelectionState(folderPath);
            const checkbox = item.querySelector('.folder-checkbox');
            
            if (state === 'all') {
                item.classList.add('selected');
                item.classList.remove('partial');
                if (checkbox) {
                    checkbox.checked = true;
                    checkbox.indeterminate = false;
                }
            } else if (state === 'some') {
                item.classList.remove('selected');
                item.classList.add('partial');
                if (checkbox) {
                    checkbox.checked = false;
                    checkbox.indeterminate = true;
                }
            } else {
                item.classList.remove('selected');
                item.classList.remove('partial');
                if (checkbox) {
                    checkbox.checked = false;
                    checkbox.indeterminate = false;
                }
            }
        });
        
        // Update selection count - show files + folders
        const countEl = document.getElementById('selected-count');
        if (countEl) {
            const fileCount = this.selectedFiles.size;
            const folderCount = this.selectedFolders.size;
            if (folderCount > 0 && fileCount > 0) {
                countEl.textContent = `${fileCount} files + ${folderCount} folders`;
            } else if (folderCount > 0) {
                countEl.textContent = `${folderCount} folder${folderCount > 1 ? 's' : ''}`;
            } else {
                countEl.textContent = fileCount;
            }
        }
        
        // Update transcribe button - enable if any selection
        const btn = document.getElementById('btn-transcribe');
        if (btn) btn.disabled = this.selectedFiles.size === 0 && this.selectedFolders.size === 0;
    }
    
    async startTranscription() {
        if (this.selectedFiles.size === 0 && this.selectedFolders.size === 0) {
            this.showToast('No files or folders selected', 'error');
            return;
        }
        
        const language = document.getElementById('language-select')?.value ?? 'en';
        const notifyBazarr = document.getElementById('notify-bazarr')?.checked ?? true;
        const skipIfExists = document.getElementById('skip-existing')?.checked ?? true;
        
        // Filter out files that are under a selected folder (to avoid duplicates)
        // Backend will expand folders, so we don't need to send individual files under them
        const folders = Array.from(this.selectedFolders);
        const files = Array.from(this.selectedFiles).filter(filePath => {
            // Exclude file if any selected folder is its parent
            return !folders.some(folderPath => filePath.startsWith(folderPath + '/'));
        });
        
        try {
            const response = await fetch('/api/batch/submit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    files: files,
                    folders: folders,
                    language: language,
                    notify_bazarr: notifyBazarr,
                    skip_if_exists: skipIfExists,
                }),
            });
            
            if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || 'Submission failed');
            }
            
            const result = await response.json();
            
            this.showToast(`Started ${result.job_count} transcription jobs`, 'success');
            this.clearSelection();
            
            // Add session to tracking
            this.sessions.set(result.session_id, {
                id: result.session_id,
                total: result.job_count,
                jobs: result.jobs,
            });
            
            // Update sessions list
            this.updateSessionsList();
            
        } catch (error) {
            console.error('Failed to start transcription:', error);
            this.showToast(`Error: ${error.message}`, 'error');
        }
    }
    
    async updateSessionsList() {
        const container = document.getElementById('sessions-list');
        
        if (this.sessions.size === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <p>No active transcription sessions.</p>
                    <p class="hint">Select files and click "Start Transcription" to begin.</p>
                </div>
            `;
            return;
        }
        
        // Fetch latest status for all sessions
        for (const [sessionId] of this.sessions) {
            try {
                const response = await fetch(`/api/batch/session/${sessionId}`);
                if (response.ok) {
                    const status = await response.json();
                    this.sessions.set(sessionId, status);
                } else if (response.status === 404) {
                    this.sessions.delete(sessionId);
                }
            } catch (error) {
                console.error(`Failed to fetch session ${sessionId}:`, error);
            }
        }
        
        // Render sessions
        let html = '';
        for (const [sessionId, session] of this.sessions) {
            // Count cancelled jobs
            const cancelledCount = (session.jobs || []).filter(j => j.status === 'cancelled').length;
            
            const progress = session.total_jobs > 0 
                ? Math.round((session.completed + session.failed + cancelledCount) / session.total_jobs * 100) 
                : 0;
            
            // Check if session is still in progress (has pending/active jobs)
            const inProgress = (session.jobs || []).some(j => 
                ['pending', 'extracting', 'uploading', 'transcribing'].includes(j.status)
            );
            
            const statusClass = session.failed > 0 ? 'has-errors' : 
                               (session.completed === session.total_jobs || !inProgress) ? 'complete' : 'in-progress';
            
            const sourceLabel = session.source === 'bazarr' ? 'Bazarr' : 'Batch';
            const sourceClass = session.source === 'bazarr' ? 'source-bazarr' : 'source-ui';
            
            // Show cancel button only for in-progress sessions
            const cancelButton = inProgress 
                ? `<button class="btn-cancel" onclick="app.cancelSession('${sessionId}')">CANCEL</button>`
                : '';
            
            html += `
                <div class="session-card ${statusClass}" data-session-id="${sessionId}">
                    <div class="session-header">
                        <span class="session-id"><span class="session-source ${sourceClass}">${sourceLabel}</span> Session ${sessionId}</span>
                        <span class="session-stats">
                            ✅ ${session.completed} / ${session.total_jobs}
                            ${session.failed > 0 ? `❌ ${session.failed}` : ''}
                            ${cancelledCount > 0 ? `🚫 ${cancelledCount}` : ''}
                            ${cancelButton}
                        </span>
                    </div>
                    <div class="progress-bar">
                        <div class="progress-fill" style="width: ${progress}%"></div>
                    </div>
                    <div class="session-jobs">
                        ${this.renderSkippedFiles(session.skipped || [])}
                        ${this.renderSessionJobs(session.jobs || [])}
                    </div>
                </div>
            `;
        }
        
        container.innerHTML = html;
    }
    
    renderSkippedFiles(skipped) {
        if (!skipped || skipped.length === 0) return '';
        
        return skipped.map(item => {
            const fileName = item.file_path.split('/').pop();
            return `
                <div class="job-item skipped">
                    <span class="job-status">⏭️</span>
                    <span class="job-name" title="${item.file_path}">${fileName}</span>
                    <span class="job-status-text skipped-reason">${this.escapeHtml(item.reason)}</span>
                    <div class="job-progress"></div>
                </div>
            `;
        }).join('');
    }
    
    renderSessionJobs(jobs) {
        if (!jobs || jobs.length === 0) return '';
        
        return jobs.map(job => {
            const fileName = job.file_path.split('/').pop();
            const statusIcon = this.getStatusIcon(job.status);
            const progressStyle = job.progress > 0 && job.status !== 'completed' && job.status !== 'failed'
                ? `style="width: ${job.progress}%"` : '';
            
            const statusText = job.status_text || '';
            const statusTextHtml = statusText && job.status !== 'completed' && job.status !== 'failed'
                ? `<span class="job-status-text">${this.escapeHtml(statusText)}</span>`
                : '';
            
            const errorHtml = job.status === 'failed' && job.error 
                ? `<div class="job-error-message">${this.escapeHtml(job.error)}</div>` 
                : '';
            
            // Show media server refresh status for completed jobs
            let refreshHtml = '';
            if (job.status === 'completed' && job.media_refresh_status) {
                const entries = Object.entries(job.media_refresh_status);
                const refreshed = entries.filter(([_, success]) => success).map(([server]) => server);
                const failed = entries.filter(([_, success]) => !success).map(([server]) => server);
                
                if (refreshed.length > 0) {
                    refreshHtml = `<span class="job-refresh success" title="Refreshed: ${refreshed.join(', ')}">🔄</span>`;
                } else if (failed.length > 0) {
                    // Media servers configured but item not found
                    refreshHtml = `<span class="job-refresh warning" title="Not found in: ${failed.join(', ')}">⚠️</span>`;
                }
            }
            
            // Show job ID (first 8 chars) for correlation with logs
            const jobId = job.id ? job.id.substring(0, 8) : '';
            
            return `
                <div class="job-item ${job.status}">
                    <span class="job-status">${statusIcon}</span>
                    <span class="job-id" title="Job ID: ${job.id}">[${jobId}]</span>
                    <span class="job-name" title="${job.file_path}">${fileName}</span>
                    ${statusTextHtml}
                    ${refreshHtml}
                    ${job.status === 'failed' ? '<span class="job-error">⚠️</span>' : ''}
                    <div class="job-progress" ${progressStyle}></div>
                </div>
                ${errorHtml}
            `;
        }).join('');
    }
    
    getStatusIcon(status) {
        switch (status) {
            case 'pending': return '⏳';
            case 'extracting': return '🎵';
            case 'uploading': return '☁️';
            case 'transcribing': return '<span class="spinner"></span>';
            case 'completed': return '✅';
            case 'failed': return '❌';
            case 'cancelled': return '🚫';
            default: return '❓';
        }
    }
    
    async cancelSession(sessionId) {
        if (!confirm('Cancel this session? Pending jobs will be cancelled and Azure resources will be cleaned up.')) {
            return;
        }
        
        try {
            const response = await fetch(`/api/batch/session/${sessionId}/cancel`, {
                method: 'POST',
            });
            
            if (response.ok) {
                const result = await response.json();
                this.showToast(
                    `Cancelled ${result.cancelled_jobs} job(s), cleaned up ${result.cleaned_blobs} blob(s)`,
                    'success'
                );
                // Refresh immediately
                await this.updateSessionsList();
            } else {
                const error = await response.json();
                this.showToast(`Failed to cancel: ${error.detail}`, 'error');
            }
        } catch (error) {
            console.error('Failed to cancel session:', error);
            this.showToast('Failed to cancel session', 'error');
        }
    }
    
    clearCompletedSessions() {
        for (const [sessionId, session] of this.sessions) {
            // Count cancelled jobs for progress calculation
            const cancelledCount = (session.jobs || []).filter(j => j.status === 'cancelled').length;
            
            if (session.completed === session.total_jobs || 
                (session.completed + session.failed + cancelledCount) === session.total_jobs) {
                // Delete from server
                fetch(`/api/batch/session/${sessionId}`, { method: 'DELETE' })
                    .catch(console.error);
                this.sessions.delete(sessionId);
            }
        }
        this.updateSessionsList();
    }
    
    startPolling() {
        // Poll every 2 seconds
        this.pollInterval = setInterval(() => {
            if (this.sessions.size > 0) {
                this.updateSessionsList();
            }
        }, 2000);
    }
    
    formatSize(bytes) {
        if (!bytes) return '';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;
        
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }
        
        return `${size.toFixed(1)} ${units[unitIndex]}`;
    }
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    showToast(message, type = 'info') {
        const container = document.getElementById('toast-container');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        
        container.appendChild(toast);
        
        // Auto-remove after 4 seconds
        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.app = new SubGenApp();
});
