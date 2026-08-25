        // ocr.js is loaded immediately before this module. Keep its public badge
        // renderers, but normalize every image path at the cascade boundary so
        // stored answer Markdown and API payloads cannot break out through a
        // filename/title interpolation in the legacy badge markup.
        (() => {
            // 多文件队列推进函数：定义于 setupImportFileHandlers 内部（需闭包 pendingFiles 等局部状态），
            // 但会被其外层的 runAIPaperParse / pollPdfTaskStatus 调用。提升到 IIFE 顶层变量，
            // 让嵌套声明改为赋值，从而对外层调用点可见（否则 typeof 守卫判非函数、调用被静默跳过，队列卡在第一份）。
            let advanceQueueAfterParse = null;

            // 目录竖栏标签工具：把文件序号转中文、定位文件分组、生成「（中文文件序号）题型缩写+文件内连续序号」
            function numberToChinese(n) {
                n = Number(n) || 0;
                if (n <= 0) return String(n);
                const digits = ['零', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
                if (n <= 10) return n === 10 ? '十' : digits[n];
                if (n < 20) return '十' + digits[n - 10];
                if (n < 100) {
                    const tens = Math.floor(n / 10);
                    const ones = n % 10;
                    return (tens === 1 ? '十' : digits[tens] + '十') + (ones === 0 ? '' : digits[ones]);
                }
                return String(n);
            }

            // 返回某题所属文件序号（从 1 起）与文件内连续序号（从 1 起）
            function getParsedQuestionFileInfo(index) {
                let fileSeq = index + 1;
                let fileNo = 1;
                if (Array.isArray(parsedFileGroups) && parsedFileGroups.length > 0) {
                    let gi = parsedFileGroups.findIndex(g => index >= g.startIndex && index < g.startIndex + (g.count || 0));
                    if (gi === -1) {
                        gi = -1;
                        for (let k = 0; k < parsedFileGroups.length; k++) {
                            if (parsedFileGroups[k].startIndex <= index) gi = k;
                        }
                    }
                    if (gi !== -1) {
                        fileNo = gi + 1;
                        fileSeq = index - parsedFileGroups[gi].startIndex + 1;
                    }
                }
                return { fileNo, fileSeq };
            }

            // 生成目录竖栏标签：多文件加「（文件中文序号）」前缀，单文件不加；题型缩写 选/填/解/未
            function getParsedQuestionCatalogLabel(index) {
                const q = parsedQuestionsData[index];
                if (!q) return '';
                const TYPE_LABEL = {
                    single_choice: '选',
                    multi_choice: '选',
                    fill_in_blank: '填',
                    detailed_answer: '解'
                };
                const typeAbbr = TYPE_LABEL[q.question_type] || '未';
                const { fileNo, fileSeq } = getParsedQuestionFileInfo(index);
                const prefix = (Array.isArray(parsedFileGroups) && parsedFileGroups.length >= 2) ? `（${numberToChinese(fileNo)}）` : '';
                return `${prefix}${typeAbbr}${fileSeq}`;
            }

            // 暴露到全局，供全局作用域的 renderSingleParsedCard 调用（与项目 window.__isMultiFileQueueMode 等暴露方式一致）
            window.numberToChinese = numberToChinese;
            window.getParsedQuestionFileInfo = getParsedQuestionFileInfo;
            window.getParsedQuestionCatalogLabel = getParsedQuestionCatalogLabel;

            const renderContentBadges = window.renderIllustrationBadges;
            if (typeof renderContentBadges === 'function') {
                window.renderIllustrationBadges = function() {
                    uploadedImages = Array.isArray(uploadedImages)
                        ? Array.from(new Set(uploadedImages.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)))
                        : [];
                    return renderContentBadges();
                };
            }

            const renderAnswerBadges = window.renderAnswerImageBadges;
            if (typeof renderAnswerBadges === 'function') {
                window.renderAnswerImageBadges = function() {
                    uploadedAnswerImages = Array.isArray(uploadedAnswerImages)
                        ? Array.from(new Set(uploadedAnswerImages.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)))
                        : [];
                    return renderAnswerBadges();
                };
            }

            window.syncAnswerImagesFromMarkdown = function() {
                const textarea = document.getElementById('editAnswerMarkdown');
                const markdown = textarea ? textarea.value : '';
                const foundImages = [];
                const imagePattern = /!\[.*?\]\(([^)]+)\)/g;
                let match;
                while ((match = imagePattern.exec(markdown)) !== null) {
                    const safePath = window.MathBankSafe.safeImageUrl(match[1]);
                    if (safePath && !foundImages.includes(safePath)) {
                        foundImages.push(safePath);
                    }
                }
                uploadedAnswerImages = foundImages;
                if (typeof window.renderAnswerImageBadges === 'function') {
                    window.renderAnswerImageBadges();
                }
            };
        })();

        function startNewQuestionWithoutPrompt() {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                window.invalidatePendingQuestionDetailLoad();
            }
            EditorState.reset();
            document.getElementById('editorTitle').textContent = '录入新数学题';
            
            document.getElementById('editContent').value = '';
            document.getElementById('editSource').value = '';
            document.getElementById('editAnswerMarkdown').value = '';
            document.getElementById('aiCustomPrompt').value = '';
            if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
            
            document.getElementById('aiOutputBox').classList.add('hidden');
            document.getElementById('ocrOutputBox').classList.add('hidden');
            
            uploadedImages = [];
            renderIllustrationBadges();
            
            document.getElementById('editQType').value = 'single_choice';
            document.getElementById('editDifficulty').value = 'easy_error';
            document.getElementById('editCompulsory').value = '';
            document.getElementById('editCompulsory').onchange();
            
            document.getElementById('editQType').dispatchEvent(new Event('change'));
            document.getElementById('editDifficulty').dispatchEvent(new Event('change'));
            clearContentOcrPreview();
            clearOcrPreview();
            
            document.getElementById('editContent').dispatchEvent(new Event('input'));
            document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            document.getElementById('editorSection').scrollTop = 0;
            
            backupEditorState(null, null);
        }

        function switchSidebarTab(tab) {
            activeSidebarTab = tab;

            const bankBtn = document.getElementById('sidebarTab-bank');
            const draftsBtn = document.getElementById('sidebarTab-drafts');

            if (tab === 'bank') {
                bankBtn.classList.add('active');
                draftsBtn.classList.remove('active-green');

                loadQuestions();
            } else {
                draftsBtn.classList.add('active-green');
                bankBtn.classList.remove('active');

                loadDrafts();
            }
        }

        // Toast Helper
        function switchWorkflowTab(tabId) {
            const tabs = ['ai', 'ocr', 'image'];
            tabs.forEach(t => {
                const btn = document.getElementById(`tabBtn-${t}`);
                const content = document.getElementById(`tabContent-${t}`);
                
                if (t === tabId) {
                    btn.className = "flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 transition-all text-brand-600 bg-white shadow-sm border border-slate-200";
                    content.classList.remove('hidden');
                } else {
                    btn.className = "flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 transition-all text-slate-600 hover:text-slate-800 hover:bg-white/50";
                    content.classList.add('hidden');
                }
            });
        }

        // Upload and drag and drop system for Illustration / OCR images
        function clearEditor() {
            // This function is called directly from index.html, so the guard
            // must live here rather than relying on the button or caller.
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (confirm('确认清空当前所有的编辑草稿吗？此操作无法撤销。')) {
                if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                    window.invalidatePendingQuestionDetailLoad();
                }
                cancelAllOcr(); // Cancel any active OCR requests!
                EditorState.reset();
                document.getElementById('editorTitle').textContent = '录入新数学题';
                
                document.getElementById('editContent').value = '';
                document.getElementById('editSource').value = '';
                document.getElementById('editAnswerMarkdown').value = '';
                document.getElementById('aiCustomPrompt').value = '';
                document.getElementById('editReview').value = '';
                if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
                document.getElementById('editRelatedQuestion').value = '';
                document.getElementById('editRelatedQuestionNum').value = '';
                document.getElementById('editReview').dispatchEvent(new Event('input')); // Hide review preview
                loadAssociatedQuestionsInList(null); // Reset associated questions panel
                
                document.getElementById('aiOutputBox').classList.add('hidden');
                document.getElementById('ocrOutputBox').classList.add('hidden');
                
                uploadedImages = [];
                renderIllustrationBadges();
                
                // Reset select lists
                document.getElementById('editQType').value = 'single_choice';
                document.getElementById('editDifficulty').value = 'easy_error';
                document.getElementById('editCompulsory').value = '';
                document.getElementById('editCompulsory').onchange();
                
                document.getElementById('editQType').dispatchEvent(new Event('change'));
                document.getElementById('editDifficulty').dispatchEvent(new Event('change'));
                
                // Clear OCR preview
                clearContentOcrPreview();
                clearOcrPreview();
                
                // Refresh previews
                document.getElementById('editContent').dispatchEvent(new Event('input'));
                document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
                
                showToast('草稿已重置');

                // Reset the original state directly from the DOM!
                backupEditorState(null, null);
            }
        }

        function startNewQuestion() {
            if (window.blockEditorSessionChangeWhileSaving && window.blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (typeof window.invalidatePendingQuestionDetailLoad === 'function') {
                window.invalidatePendingQuestionDetailLoad();
            }
            EditorState.reset();
            document.getElementById('editorTitle').textContent = '录入新数学题';

            window.lastOcrOriginalImagePath = '';
            window.contentLastCompiledTikzPath = '';
            window.answerLastCompiledTikzPath = '';
            document.getElementById('editContent').value = '';
            document.getElementById('editSource').value = '';
            document.getElementById('editAnswerMarkdown').value = '';
            document.getElementById('aiCustomPrompt').value = '';
            document.getElementById('editReview').value = '';
            if (document.getElementById('editTags')) document.getElementById('editTags').value = '';
            if (document.getElementById('editContentTikzCode')) document.getElementById('editContentTikzCode').value = '';
            if (document.getElementById('editAnswerTikzCode')) document.getElementById('editAnswerTikzCode').value = '';
            
            // Hide Content & Answer TikZ Panels
            if (document.getElementById('contentTikzContainer')) document.getElementById('contentTikzContainer').classList.add('hidden');
            if (document.getElementById('answerTikzContainer')) document.getElementById('answerTikzContainer').classList.add('hidden');
            
            if (document.getElementById('contentTikzPreviewImage')) {
                document.getElementById('contentTikzPreviewImage').classList.add('hidden');
                document.getElementById('contentTikzPreviewImage').src = '';
                document.getElementById('contentTikzPreviewPlaceholder').classList.remove('hidden');
                document.getElementById('contentTikzStatusText').textContent = '未编译';
            }
            if (document.getElementById('answerTikzPreviewImage')) {
                document.getElementById('answerTikzPreviewImage').classList.add('hidden');
                document.getElementById('answerTikzPreviewImage').src = '';
                document.getElementById('answerTikzPreviewPlaceholder').classList.remove('hidden');
                document.getElementById('answerTikzStatusText').textContent = '未编译';
            }
            
            document.getElementById('editRelatedQuestion').value = '';
            document.getElementById('editRelatedQuestionNum').value = '';
            document.getElementById('editReview').dispatchEvent(new Event('input')); // Hide review preview
            loadAssociatedQuestionsInList(null); // Reset associated questions panel
            
            // Clear hidden caches to avoid invisible leftovers when switching tabs
            document.getElementById('contentOcrResultText').textContent = '';
            document.getElementById('ocrResultText').textContent = '';
            document.getElementById('aiResultText').textContent = '';
            
            document.getElementById('aiOutputBox').classList.add('hidden');
            document.getElementById('ocrOutputBox').classList.add('hidden');
            
            uploadedImages = [];
            renderIllustrationBadges();
            
            // Reset selects
            document.getElementById('editQType').value = 'single_choice';
            document.getElementById('editDifficulty').value = 'easy_error';
            document.getElementById('editCompulsory').value = '';
            document.getElementById('editCompulsory').onchange();
            
            document.getElementById('editQType').dispatchEvent(new Event('change'));
            document.getElementById('editDifficulty').dispatchEvent(new Event('change'));
            
            // Clear OCR preview
            clearContentOcrPreview();
            clearOcrPreview();
            
            // Sync-clear all previews to avoid 250ms debounce flash
            document.getElementById('contentPreview').innerHTML = '<p class="text-slate-400 italic">在左侧框中输入，此处将实时展示最终排版效果...</p>';
            document.getElementById('paperContent').innerHTML = '<p class="text-slate-400 italic text-center py-10">输入题干内容后，此处将展示实时试卷排版效果。</p>';
            document.getElementById('answerPreview').innerHTML = '<p class="text-slate-400 italic">在左侧输入解析内容，此处将实时展示 LaTeX 渲染排版...</p>';
            document.getElementById('paperAnalysisContent').innerHTML = '<p class="text-slate-400 italic">暂无解析内容。</p>';
            
            // Refresh previews
            document.getElementById('editContent').dispatchEvent(new Event('input'));
            document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
            
            // Scroll editor into view
            document.getElementById('editorSection').scrollTop = 0;
            
            // Reload list styling selection
            loadQuestions();
            
            showToast('开始录入新数学题！');

            // Reset the original state directly from the DOM!
            backupEditorState(null, null);
        }

        // Load all questions to populate the related question dropdown list
        function refreshRelatedDropdown(selectedId = "") {
            fetch('/api/questions')
                .then(r => r.json())
                .then(questions => {
                    const dropdown = document.getElementById('editRelatedQuestion');
                    const numInput = document.getElementById('editRelatedQuestionNum');
                    if (!dropdown) return;
                    
                    dropdown.innerHTML = '<option value="">-- 选择要关联的题目 (可选) --</option>';
                    
                    let foundSelectedSeq = '';
                    
                    questions.forEach(q => {
                        // Exclude the current editing question
                        if (EditorState.questionId && q.id === EditorState.questionId) {
                            return;
                        }
                        
                        // Extract a snippet of the question stem
                        let textSnippet = q.content || '';
                        // Remove HTML/Markdown tags and LaTeX brackets to make it readable
                        textSnippet = textSnippet.replace(/[\$\#\*\_]/g, '').substring(0, 40);
                        if ((q.content || '').length > 40) textSnippet += '...';
                        
                        const optionText = `#${q.seq_num} [${getTypeText(q.question_type)}] - ${textSnippet}`;
                        const option = document.createElement('option');
                        option.value = q.id;
                        option.setAttribute('data-seq-num', q.seq_num);
                        option.textContent = optionText;
                        
                        if (String(q.id) === String(selectedId)) {
                            option.selected = true;
                            foundSelectedSeq = q.seq_num;
                        }
                        dropdown.appendChild(option);
                    });
                    
                    if (numInput) {
                        numInput.value = foundSelectedSeq;
                    }
                })
                .catch(err => {
                    console.error('Failed to load related questions list:', err);
                });
        }

        // Associate selected target question with current loaded question (bidirectional, backend + UI)
        async function associateRelatedQuestion() {
            const targetSelect = document.getElementById('editRelatedQuestion');
            const targetId = targetSelect ? targetSelect.value : '';
            if (!targetId) {
                showToast('请先在编号框或下拉框中选择要关联的目标题目', 'warning');
                return;
            }

            if (!EditorState.questionId) {
                showToast('当前正在录入新题目，请先保存本题后再建立实时关联。', 'info');
                return;
            }

            if (parseInt(targetId, 10) === parseInt(EditorState.questionId, 10)) {
                showToast('题目不能与自身建立关联', 'warning');
                return;
            }

            try {
                const formData = new FormData();
                formData.append('target_id', targetId);

                const res = await fetch(`/api/questions/${EditorState.questionId}/associate`, {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: formData
                });

                const data = await res.json();
                if (res.ok && data.status === 'success') {
                    const selectedOpt = targetSelect.options[targetSelect.selectedIndex];
                    const seqNum = selectedOpt ? selectedOpt.getAttribute('data-seq-num') : '';
                    showToast(`成功与题目 #${seqNum || targetId} 建立关联绑定！`, 'success');

                    // 刷新右侧预览区的关联变式题目卡片及下拉框
                    if (typeof loadAssociatedQuestionsInList === 'function') {
                        loadAssociatedQuestionsInList(EditorState.questionId);
                    }
                } else {
                    showToast(data.detail || data.message || '关联建立失败', 'error');
                }
            } catch (e) {
                console.error(e);
                showToast('关联请求异常: ' + e.message, 'error');
            }
        }

        // Clear the related question association (bidirectional, backend + UI)
        function clearRelatedQuestion() {
            if (!EditorState.questionId) {
                // No question loaded, just clear the UI
                const dropdown = document.getElementById('editRelatedQuestion');
                const numInput = document.getElementById('editRelatedQuestionNum');
                if (dropdown) dropdown.value = '';
                if (numInput) numInput.value = '';
                showToast('已清除关联选择', 'success');
                return;
            }

            fetch(`/api/questions/${EditorState.questionId}/associated`, { method: 'DELETE' })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        // Clear UI
                        const dropdown = document.getElementById('editRelatedQuestion');
                        const numInput = document.getElementById('editRelatedQuestionNum');
                        if (dropdown) dropdown.value = '';
                        if (numInput) numInput.value = '';

                        // Hide associated list in preview
                        const wrapper = document.getElementById('paperAssociatedWrapper');
                        const container = document.getElementById('paperAssociatedList');
                        if (wrapper) wrapper.classList.add('hidden');
                        if (container) container.innerHTML = '';

                        showToast('已解除所有关联（双向生效）', 'success');
                    } else {
                        showToast(data.detail || '解除关联失败', 'error');
                    }
                })
                .catch(err => {
                    console.error('Failed to remove association:', err);
                    showToast('解除关联失败: ' + err.message, 'error');
                });
        }

        // Fetch associated questions under transitive group and populate live preview
        function loadAssociatedQuestionsInList(questionId) {
            const wrapper = document.getElementById('paperAssociatedWrapper');
            const container = document.getElementById('paperAssociatedList');
            if (wrapper) wrapper.classList.add('hidden');
            if (container) container.innerHTML = '';
            
            if (!questionId) {
                refreshRelatedDropdown("");
                return;
            }
            
            fetch(`/api/questions/${questionId}/associated`)
                .then(r => r.json())
                .then(list => {
                    let associatedId = "";
                    if (list.length > 0) {
                        associatedId = list[0].id;
                        if (wrapper) wrapper.classList.remove('hidden');
                        
                        list.forEach(q => {
                            let cleanContent = (q.content || '').replace(/[\$\#\*\_]/g, '');
                            if (cleanContent.length > 50) cleanContent = cleanContent.substring(0, 50) + '...';

                            const item = document.createElement('div');
                            item.className = "glass-list-item p-2.5 rounded-xl text-xs text-slate-700 flex items-center justify-between";
                            item.onclick = () => selectQuestionById(q.id);
                            item.innerHTML = `
                                <div class="truncate pr-2">
                                    <span class="font-bold text-brand-600 bg-brand-50 px-1.5 py-0.5 rounded text-[10px] mr-1.5 shadow-sm">#${window.MathBankSafe.escapeText(q.seq_num)}</span>
                                    <span class="text-[10px] bg-slate-100 px-1.5 py-0.5 rounded text-slate-500 mr-1.5">${window.MathBankSafe.escapeText(getTypeText(q.question_type))}</span>
                                    <span>${window.MathBankSafe.escapeText(cleanContent)}</span>
                                </div>
                                <i class="fa-solid fa-chevron-right text-[9px] text-slate-400 shrink-0"></i>
                            `;
                            if (container) container.appendChild(item);
                        });
                    }
                    refreshRelatedDropdown(associatedId);
                })
                .catch(err => {
                    console.error('Failed to load associated questions list:', err);
                    refreshRelatedDropdown("");
                });
        }

        // Jump to select another question by ID
        function selectQuestionById(id) {
            fetch(`/api/questions/${id}`)
                .then(r => {
                    if (!r.ok) throw new Error('未找到对应的关联题目');
                    return r.json();
                })
                .then(q => {
                    checkAndSwitch(() => selectQuestion(q));
                })
                .catch(err => {
                    showToast('获取关联题目出错: ' + err.message, 'error');
                });
        }

        let questionDetailLoadSequence = 0;
        let questionDetailLoading = false;
        let saveQuestionInFlight = null;

        function blockEditorSessionChangeWhileSaving() {
            if (!saveQuestionInFlight) return false;
            showToast('题目正在保存，请等待完成后再重置或切换', 'info');
            return true;
        }
        window.blockEditorSessionChangeWhileSaving = blockEditorSessionChangeWhileSaving;

        window.isQuestionSaveInFlight = function() {
            return Boolean(saveQuestionInFlight);
        };

        function updateQuestionSaveButtonState() {
            const button = document.getElementById('saveQuestionBtn');
            if (!button) return;

            const isSaving = Boolean(saveQuestionInFlight);
            const isBusy = questionDetailLoading || isSaving;
            button.disabled = isBusy;
            button.setAttribute('aria-busy', isBusy ? 'true' : 'false');
            button.classList.toggle('opacity-60', isBusy);
            button.classList.toggle('pointer-events-none', isBusy);

            if (questionDetailLoading) {
                button.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>加载题目...</span>';
            } else if (isSaving) {
                button.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>保存中...</span>';
            } else {
                button.innerHTML = '<i class="fa-solid fa-floppy-disk"></i><span>保存</span>';
            }
        }

        function invalidatePendingQuestionDetailLoad() {
            questionDetailLoadSequence += 1;
            questionDetailLoading = false;
            updateQuestionSaveButtonState();
        }
        window.invalidatePendingQuestionDetailLoad = invalidatePendingQuestionDetailLoad;

        // Select a question to Edit & Preview. The editor identity is committed
        // only after the requested detail payload has arrived successfully.
        function selectQuestion(item) {
            if (blockEditorSessionChangeWhileSaving()) {
                return;
            }
            const requestedQuestionId = Number(item && item.id);
            if (!Number.isSafeInteger(requestedQuestionId) || requestedQuestionId <= 0) {
                showToast('无法加载题目：题目 ID 无效', 'error');
                return;
            }
            EditorState.beginTransition();
            const loadSequence = ++questionDetailLoadSequence;
            questionDetailLoading = true;
            updateQuestionSaveButtonState();

            // Lazy-load details asynchronously
            fetch(`/api/questions/${requestedQuestionId}`)
                .then(r => {
                    if (!r.ok) throw new Error('无法加载题目详情');
                    return r.json();
                })
                .then(fullItem => {
                    if (loadSequence !== questionDetailLoadSequence) {
                        return;
                    }
                    if (!fullItem || Number(fullItem.id) !== requestedQuestionId) {
                        throw new Error('题目详情与请求 ID 不匹配');
                    }
                    EditorState.useQuestion(fullItem);
                    document.getElementById('editorTitle').textContent = '编辑数学题';

                    // Clear previous OCR state only after the question switch commits.
                    clearContentOcrPreview();
                    clearOcrPreview();

                    // Sync the active card highlight after the matching details load.
                    const questionsList = document.getElementById('questionsList');
                    const listCards = questionsList ? questionsList.children : [];
                    for (let i = 0; i < listCards.length; i++) {
                        const card = listCards[i];
                        if (parseInt(card.dataset.id, 10) === requestedQuestionId) {
                            card.classList.add('active');
                        } else {
                            card.classList.remove('active');
                        }
                    }
                    window.lastOcrOriginalImagePath = '';
                    window.contentLastCompiledTikzPath = '';
                    window.answerLastCompiledTikzPath = '';
                    // Load values to editor
                    document.getElementById('editContent').value = fullItem.content;
                    document.getElementById('editSource').value = fullItem.source || '';
                    document.getElementById('editAnswerMarkdown').value = fullItem.answer_markdown || '';
                    document.getElementById('editReview').value = fullItem.review || '';
                    if (document.getElementById('editContentTikzCode')) {
                        document.getElementById('editContentTikzCode').value = fullItem.tikz_code || '';
                    }
                    if (document.getElementById('editAnswerTikzCode')) {
                        document.getElementById('editAnswerTikzCode').value = '';
                    }
                    
                    // Reset TikZ Preview on load
                    if (document.getElementById('contentTikzPreviewImage')) {
                        document.getElementById('contentTikzPreviewImage').classList.add('hidden');
                        document.getElementById('contentTikzPreviewImage').src = '';
                        document.getElementById('contentTikzPreviewPlaceholder').classList.remove('hidden');
                        document.getElementById('contentTikzStatusText').textContent = fullItem.tikz_code ? '已加载' : '未编译';
                    }
                    if (document.getElementById('answerTikzPreviewImage')) {
                        document.getElementById('answerTikzPreviewImage').classList.add('hidden');
                        document.getElementById('answerTikzPreviewImage').src = '';
                        document.getElementById('answerTikzPreviewPlaceholder').classList.remove('hidden');
                        document.getElementById('answerTikzStatusText').textContent = '未编译';
                    }
                    
                    uploadedImages = Array.isArray(fullItem.image_paths)
                        ? fullItem.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                        : [];
                    renderIllustrationBadges();
                    
                    // Show or hide Content TikZ container dynamically on load
                    const contentContainer = document.getElementById('contentTikzContainer');
                    if (contentContainer) {
                        const hasOriginalImage = uploadedImages.some(path => !path.includes('/tikz_'));
                        if (fullItem.tikz_code || hasOriginalImage) {
                            contentContainer.classList.remove('hidden');
                        } else {
                            contentContainer.classList.add('hidden');
                        }
                    }
                    const answerContainer = document.getElementById('answerTikzContainer');
                    if (answerContainer) {
                        answerContainer.classList.add('hidden');
                    }
                    
                    // Cascade bindings
                    document.getElementById('editQType').value = fullItem.question_type;
                    document.getElementById('editDifficulty').value = fullItem.difficulty;
                    if (document.getElementById('editTags')) {
                        document.getElementById('editTags').value = fullItem.tags || '';
                    }

                    // 初始化知识点 / 解题方法 多标签编辑
                    setupEditTagInput('editKnowledgeTags', 'editKnowledgeTagsChips', 'editKnowledgeTagInput', fullItem.knowledge_list || '');
                    setupEditTagInput('editSolveMethodTags', 'editSolveMethodTagsChips', 'editSolveMethodTagInput', fullItem.solve_method || '');

                    const compSelect = document.getElementById('editCompulsory');
                    const chapSelect = document.getElementById('editChapter');
                    const knowSelect = document.getElementById('editKnowledge');
                    
                    // In case the categories in item are not in tree yet, add them temporarily
                    // Repopulate with clean categoryTree
                    populateCategoryDropdowns();
                    
                    compSelect.value = fullItem.category_compulsory || '';
                    compSelect.onchange();
                    chapSelect.value = fullItem.category_chapter || '';
                    chapSelect.onchange();
                    knowSelect.value = fullItem.category_knowledge || '';
                    
                    // Dispatch input previews or update synchronously
                    if (typeof window.updateContentPreview === 'function') {
                        window.updateContentPreview();
                    } else {
                        document.getElementById('editContent').dispatchEvent(new Event('input'));
                    }
                    if (typeof window.updateAnswerPreview === 'function') {
                        window.updateAnswerPreview();
                    } else {
                        document.getElementById('editAnswerMarkdown').dispatchEvent(new Event('input'));
                    }
                    if (typeof window.updateReviewPreview === 'function') {
                        window.updateReviewPreview();
                    } else {
                        document.getElementById('editReview').dispatchEvent(new Event('input'));
                    }
                    
                    // Render editor metadata through the single shared preview path.
                    renderEditorPaperMeta();
                    
                    // Load associated questions list and handle group selection
                    loadAssociatedQuestionsInList(fullItem.id);
                    
                    // Scroll editor
                    document.getElementById('editorSection').scrollTop = 0;
                    
                    // Scroll to card active or highlight in current view
                    showToast(`题目 #${fullItem.seq_num} 载入成功`);
         
                    // Backup the original loaded question state directly from the DOM!
                    backupEditorState(fullItem.id, null);
                })
                .catch(err => {
                    if (loadSequence !== questionDetailLoadSequence) return;
                    console.error('Failed to load full question details:', err);
                    showToast('获取题目详情失败: ' + err.message, 'error');
                })
                .finally(() => {
                    if (loadSequence !== questionDetailLoadSequence) return;
                    questionDetailLoading = false;
                    updateQuestionSaveButtonState();
                });
        }

        window.reloadCurrentQuestionSilently = function() {
            if (!EditorState.questionId) return false;
            if (typeof window.isEditorModified === 'function' && window.isEditorModified()) {
                showToast('配置已更新；当前未保存的编辑内容已保留，保存或重新打开题目后即可同步', 'info');
                return false;
            }
            selectQuestion({
                id: EditorState.questionId,
                seq_num: EditorState.seqNum,
                created_at: EditorState.createdAt
            });
            return true;
        };

        // Save/Update Question in SQLite (returns Promise)
        function saveQuestion(skipCheck = false) {
            if (questionDetailLoading) {
                showToast('题目详情仍在加载，请稍候再保存', 'info');
                return Promise.resolve(false);
            }
            if (saveQuestionInFlight) {
                return saveQuestionInFlight;
            }

            const saveOperation = (async () => {
                const editorSession = EditorState.snapshot();
                const content = document.getElementById('editContent').value;
                const qtype = document.getElementById('editQType').value;
                const compulsory = document.getElementById('editCompulsory').value;
                const chapter = document.getElementById('editChapter').value;
                const knowledge = document.getElementById('editKnowledge').value;
                const difficulty = document.getElementById('editDifficulty').value;
                const source = document.getElementById('editSource').value;
                const answerMarkdown = document.getElementById('editAnswerMarkdown').value;
                const review = document.getElementById('editReview').value;
                const relatedQuestionId = document.getElementById('editRelatedQuestion').value;
                const tikzCode = document.getElementById('editContentTikzCode') ? document.getElementById('editContentTikzCode').value : '';
                const tags = document.getElementById('editTags') ? document.getElementById('editTags').value.trim() : '';
                const knowledge_list = window._editKnowledgeTags ? window._editKnowledgeTags.join(',') : '';
                const solve_method = window._editSolveMethodTags ? window._editSolveMethodTags.join(',') : '';
                
                if (!content.trim()) {
                    showToast('保存失败：题干内容不能为空！', 'error');
                    return false;
                }
                
                // Check if Compulsory or Chapter classifications are missing
                if (!skipCheck && (!compulsory || !chapter)) {
                    const choice = await showMissingCompulsoryModal();
                    if (choice === 'manual') {
                        if (!compulsory) {
                            const compSelect = document.getElementById('editCompulsory');
                            if (compSelect) {
                                compSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                // Add premium temporary focus highlight (using brand color ring)
                                compSelect.classList.remove('border-slate-200');
                                compSelect.classList.add('ring-2', 'ring-brand-500', 'border-brand-500');
                                setTimeout(() => {
                                    compSelect.classList.remove('ring-2', 'ring-brand-500', 'border-brand-500');
                                    compSelect.classList.add('border-slate-200');
                                }, 2500);
                                compSelect.focus();
                            }
                        } else if (!chapter) {
                            const chapSelect = document.getElementById('editChapter');
                            if (chapSelect) {
                                chapSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                // Add premium temporary focus highlight (using brand color ring)
                                chapSelect.classList.remove('border-slate-200');
                                chapSelect.classList.add('ring-2', 'ring-brand-500', 'border-brand-500');
                                setTimeout(() => {
                                    chapSelect.classList.remove('ring-2', 'ring-brand-500', 'border-brand-500');
                                    chapSelect.classList.add('border-slate-200');
                                }, 2500);
                                chapSelect.focus();
                            }
                        }
                    } else if (choice === 'ai') {
                        // Automatically open AI classify modal and trigger AI analysis
                        openClassifyModal();
                        runAIClassify();
                    }
                    return false;
                }

                const requestBackupSnapshot = Object.freeze({
                    content: content,
                    answer_markdown: answerMarkdown,
                    review: review,
                    question_type: qtype,
                    difficulty: difficulty,
                    source: source,
                    category_compulsory: compulsory,
                    category_chapter: chapter,
                    category_knowledge: knowledge,
                    image_paths: JSON.stringify(Array.from(uploadedImages)),
                    tags: tags
                });
                
                const formData = new FormData();
                formData.append('content', content);
                formData.append('question_type', qtype);
                formData.append('category_compulsory', compulsory);
                formData.append('category_chapter', chapter);
                formData.append('category_knowledge', knowledge);
                formData.append('difficulty', difficulty);
                formData.append('source', source);
                formData.append('answer_markdown', answerMarkdown);
                formData.append('review', review);
                formData.append('related_question_id', relatedQuestionId);
                formData.append('tikz_code', tikzCode);
                formData.append('tags', tags);
                formData.append('knowledge_list', knowledge_list);
                formData.append('solve_method', solve_method);
                const combinedImages = Array.from(new Set([
                    ...uploadedImages,
                    ...(typeof uploadedAnswerImages !== 'undefined' ? uploadedAnswerImages : [])
                ].map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)));
                formData.append('image_paths', JSON.stringify(combinedImages));
                
                let url = '/api/questions';
                let method = 'POST';
                
                if (editorSession.questionId) {
                    url = `/api/questions/${editorSession.questionId}`;
                    method = 'PUT';
                }

                try {
                    const response = await fetch(url, {
                        method: method,
                        body: formData
                    });
                    if (!response.ok) {
                        let message = `服务器返回错误 HTTP ${response.status}`;
                        try {
                            const errorData = await response.json();
                            message = errorData.detail || errorData.message || message;
                        } catch (parseError) {
                            // Keep the HTTP fallback when the error body is not JSON.
                        }
                        throw new Error(message);
                    }

                    const data = await response.json();
                    if (data.status === 'success') {
                        showToast(editorSession.questionId ? '题目已成功更新！' : '题目已成功保存！');
                        const editorSessionStillCurrent = EditorState.isCurrent(editorSession);
                        const requestStillVisible = editorSessionStillCurrent &&
                            window.editorMatchesBackupSnapshot(requestBackupSnapshot);

                        // Never clear OCR or overwrite fields belonging to a newer
                        // edit/switch that happened after this request started.
                        if (requestStillVisible) {
                            clearContentOcrPreview();
                            clearOcrPreview();
                        }

                        // Delete draft if it was saved from a draft
                        if (editorSession.draftId) {
                            let drafts = getLocalStorageDrafts();
                            drafts = drafts.filter(d => d.id !== editorSession.draftId);
                            setLocalStorageDrafts(drafts);
                            updateDraftCountBadge();
                            if (EditorState.draftId === editorSession.draftId) {
                                EditorState.clearDraft();
                            }
                        }

                        // Reload list, dropdown, and autocomplete selectors
                        loadQuestions();
                        loadCategories();
                        refreshRelatedDropdown();

                        if (!editorSession.questionId && editorSessionStillCurrent) {
                            // The POST response already contains the complete saved
                            // question. Commit only its identity and saved baseline;
                            // never start a second detail request that could overwrite
                            // input typed after the POST response arrived.
                            EditorState.useQuestion(data.question);
                            backupEditorState(data.question.id, null, requestBackupSnapshot);
                            document.getElementById('editorTitle').textContent = '编辑数学题';
                            if (!requestStillVisible) {
                                showToast('题目已保存；保存后继续输入的内容仍待再次保存', 'info');
                            }
                        } else if (editorSessionStillCurrent) {
                            backupEditorState(data.question.id, null, requestBackupSnapshot);
                        }
                        return true;
                    } else {
                        showToast('保存题目失败: ' + (data.detail || data.message || '未知错误'), 'error');
                        return false;
                    }
                } catch (err) {
                    showToast('保存数据出错: ' + err.message, 'error');
                    return false;
                }
            })();

            saveQuestionInFlight = saveOperation.finally(() => {
                saveQuestionInFlight = null;
                updateQuestionSaveButtonState();
            });
            updateQuestionSaveButtonState();
            return saveQuestionInFlight;
        }

        // AI classification modal handlers
        let temporaryClassifyData = null;
        let temporaryClassifyQuestionType = null;

        function setClassifyApplyEnabled(enabled) {
            const applyBtn = document.getElementById('classifyApplyButton');
            if (!applyBtn) return;
            applyBtn.disabled = !enabled;
            applyBtn.setAttribute('aria-disabled', enabled ? 'false' : 'true');
        }

        function resetClassifiedChoiceType() {
            temporaryClassifyQuestionType = null;
            ['classifySingleChoiceBtn', 'classifyMultiChoiceBtn'].forEach(id => {
                const button = document.getElementById(id);
                if (!button) return;
                button.setAttribute('aria-checked', 'false');
            });
        }

        function selectClassifiedChoiceType(questionType) {
            if (questionType !== 'single_choice' && questionType !== 'multi_choice') return;
            temporaryClassifyQuestionType = questionType;
            const selectedId = questionType === 'single_choice'
                ? 'classifySingleChoiceBtn'
                : 'classifyMultiChoiceBtn';
            ['classifySingleChoiceBtn', 'classifyMultiChoiceBtn'].forEach(id => {
                const button = document.getElementById(id);
                if (!button) return;
                const selected = id === selectedId;
                button.setAttribute('aria-checked', selected ? 'true' : 'false');
            });
            setClassifyApplyEnabled(true);
        }

        function openClassifyModal() {
            const modal = document.getElementById('aiClassifyModal');
            modal.classList.remove('hidden');
            window.MathBankModal.open(modal, { onEscape: closeClassifyModal });
            
            // Reset modal states
            document.getElementById('classifyLoading').classList.add('hidden');
            document.getElementById('classifyResult').classList.add('hidden');
            document.getElementById('classifyAIButton').classList.remove('hidden');
            document.getElementById('classifyApplyButton').classList.add('hidden');
            document.getElementById('choiceTypeConfirm').classList.add('hidden');
            document.getElementById('unknownQuestionFormNotice').classList.add('hidden');
            temporaryClassifyData = null;
            resetClassifiedChoiceType();
            setClassifyApplyEnabled(true);
            
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closeClassifyModal() {
            const modal = document.getElementById('aiClassifyModal');
            window.MathBankModal.close(modal);
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }

        function runAIClassify() {
            const content = document.getElementById('editContent').value;
            const loading = document.getElementById('classifyLoading');
            const resultBox = document.getElementById('classifyResult');
            const aiBtn = document.getElementById('classifyAIButton');
            const applyBtn = document.getElementById('classifyApplyButton');
            
            loading.classList.remove('hidden');
            aiBtn.classList.add('hidden');
            resultBox.classList.add('hidden');
            
            const formData = new FormData();
            formData.append('content', content);
            
            fetch('/api/ai/classify', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                loading.classList.add('hidden');
                
                if (data.status === 'success') {
                    temporaryClassifyData = data;
                    document.getElementById('recCompulsory').textContent = data.compulsory;
                    document.getElementById('recChapter').textContent = data.chapter;
                    const formLabels = {
                        'choice': '选择题',
                        'fill_in_blank': '填空题',
                        'detailed_answer': '解答题',
                        'unknown': '待手动确认'
                    };
                    const questionForm = formLabels[data.question_form] ? data.question_form : 'unknown';
                    document.getElementById('recQuestionForm').textContent = formLabels[questionForm];
                    document.getElementById('recQuestionFormSource').textContent = data.question_form_source === 'structure'
                        ? '结构规则识别'
                        : 'AI 建议';

                    const choiceConfirm = document.getElementById('choiceTypeConfirm');
                    const unknownNotice = document.getElementById('unknownQuestionFormNotice');
                    choiceConfirm.classList.toggle('hidden', questionForm !== 'choice');
                    unknownNotice.classList.toggle('hidden', questionForm !== 'unknown');
                    resetClassifiedChoiceType();

                    if (questionForm === 'fill_in_blank') {
                        temporaryClassifyQuestionType = 'fill_in_blank';
                        setClassifyApplyEnabled(true);
                    } else if (questionForm === 'detailed_answer') {
                        temporaryClassifyQuestionType = 'detailed_answer';
                        setClassifyApplyEnabled(true);
                    } else if (questionForm === 'choice') {
                        setClassifyApplyEnabled(false);
                    } else {
                        setClassifyApplyEnabled(true);
                    }
                    
                    resultBox.classList.remove('hidden');
                    applyBtn.classList.remove('hidden');
                } else {
                    showToast(data.message || 'AI 智能分类分析失败！', 'error');
                    aiBtn.classList.remove('hidden');
                }
            })
            .catch(err => {
                loading.classList.add('hidden');
                aiBtn.classList.remove('hidden');
                showToast('AI 分类出错: ' + err, 'error');
            });
        }

        function applyClassifyRecommendation() {
            if (!temporaryClassifyData) return;
            if (temporaryClassifyData.question_form === 'choice' && !temporaryClassifyQuestionType) {
                showToast('请先确认此题是单选题还是多选题！', 'error');
                return;
            }
            
            const compSelect = document.getElementById('editCompulsory');
            const chapSelect = document.getElementById('editChapter');
            const knowSelect = document.getElementById('editKnowledge');
            const qtypeSelect = document.getElementById('editQType');
            
            const comp = temporaryClassifyData.compulsory;
            const chap = temporaryClassifyData.chapter;
            
            // Ensure nodes exist in local dictionary structure
            if (!categoryTree[comp]) {
                categoryTree[comp] = {};
            }
            if (!categoryTree[comp][chap]) {
                categoryTree[comp][chap] = [];
            }
            
            populateCategoryDropdowns();
            
            compSelect.value = comp;
            compSelect.onchange();
            chapSelect.value = chap;
            chapSelect.onchange();
            knowSelect.value = chap; // Default empty third level (小节) to chapter name

            if (temporaryClassifyQuestionType && qtypeSelect) {
                qtypeSelect.value = temporaryClassifyQuestionType;
                qtypeSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }
            
            closeClassifyModal();
            showToast('教材章节及题型已确认！');
            
            // Save question now with skipCheck = true
            setTimeout(() => {
                saveQuestion(true);
            }, 250);
        }

        window.selectClassifiedChoiceType = selectClassifiedChoiceType;

        // Delete Question
        function deleteQuestion(id) {
            if (blockEditorSessionChangeWhileSaving()) {
                return;
            }
            if (confirm('确认要在本地库中彻底删除此题目吗？不可恢复！')) {
                fetch(`/api/questions/${id}`, {
                    method: 'DELETE'
                })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        showToast('题目已成功删除！');
                        if (EditorState.questionId === id) {
                            startNewQuestion();
                        } else {
                            loadQuestions();
                            loadCategories();
                        }
                        refreshRelatedDropdown();
                    } else {
                        showToast(data.message, 'error');
                    }
                })
                .catch(err => {
                    showToast('删除题目出错: ' + err, 'error');
                });
            }
        }

        // Toggle Paper Analysis reveal
        function togglePaperAnalysis() {
            const content = document.getElementById('paperAnalysisContent');
            const icon = document.getElementById('analysisIcon');
            const text = document.getElementById('analysisText');
            const copyBtn = document.getElementById('copyAnalysisBtn');
            
            if (content.classList.contains('hidden')) {
                content.classList.remove('hidden');
                icon.className = 'fa-solid fa-eye-slash';
                text.textContent = '隐藏解析';
                if (copyBtn) copyBtn.classList.remove('hidden');
            } else {
                content.classList.add('hidden');
                icon.className = 'fa-solid fa-eye';
                text.textContent = '查看解析';
                if (copyBtn) copyBtn.classList.add('hidden');
            }
        }

        // ==========================================
        // LaTeX BATCH IMPORT & AI PARSE JS LOGIC
        // ==========================================
        let batchSelectedImages = [];
        let parsedQuestionsData = [];
        let parsedQuestionsGeneration = 0;
        // 多文件批量导入时，按拆解顺序记录“每个文件对应哪些题”，用于在审查列表里分组展示。
        // 结构：{ name(来源文件名), startIndex(在 parsedQuestionsData 中的起始下标), count(本题数) }
        let parsedFileGroups = [];
        // 拆解结果审查筛选状态：'unimported'(默认,只看未导入) | 'imported' | 'all'
        // 仅控制卡片可见性，不改动 parsedQuestionsData 数组下标（避免文件分组错位）。
        window.__parsedReviewFilter = window.__parsedReviewFilter || 'unimported';
        const parsedQuestionSaveInFlight = new Map();
        let allSourcesList = [];

        function replaceParsedQuestions(nextQuestions) {
            // 单文件全量替换：清掉上一轮（可能是多文件）残留的页面图映射
            if (window.pdfPageImagesMap) window.pdfPageImagesMap = {};
            parsedQuestionsGeneration += 1;
            parsedQuestionsData = Array.isArray(nextQuestions) ? nextQuestions : [];
            // 单文件全量替换时清空分组记录（分组仅用于多文件批量导入）
            parsedFileGroups = [];
            return parsedQuestionsGeneration;
        }

        // 多文件模式：把新拆解的题追加到现有审查列表（不清空），并为每题标注来源文件。
        // 返回 { generation, startIndex, count }，供增量渲染使用。
        function appendParsedQuestions(newQuestions, sourceFile) {
            parsedQuestionsGeneration += 1;
            const startIndex = parsedQuestionsData.length;
            const list = Array.isArray(newQuestions) ? newQuestions : [];
            list.forEach(q => {
                if (q && typeof q === 'object') {
                    q.source_file = sourceFile || '';
                    // 默认把来源文件名填入 source（来源输入框），便于人工核对与入库
                    if (!q.source) q.source = sourceFile || '';
                }
                parsedQuestionsData.push(q);
            });
            // 记录本批题归属的文件分组（用于在审查列表里插入分组头）
            if (list.length > 0) {
                parsedFileGroups.push({
                    name: sourceFile || '',
                    startIndex: startIndex,
                    count: list.length
                });
            }
            window.__currentParseStartIndex = startIndex;
            return { generation: parsedQuestionsGeneration, startIndex, count: list.length };
        }

        function isParsedQuestionSaveContextCurrent(generation, index, question) {
            return generation === parsedQuestionsGeneration &&
                   parsedQuestionsData[index] === question;
        }

        function blockImportResetWhileSaving() {
            if (parsedQuestionSaveInFlight.size === 0) return false;
            showToast(`仍有 ${parsedQuestionSaveInFlight.size} 道题正在入库，请等待完成后再重置或关闭`, 'info');
            return true;
        }

        function openImportModal() {
            const modal = document.getElementById('latexImportModal');
            // 打开弹窗先清空上一批残留的拆解结果、日志与多文件队列，
            // 避免旧题目和新批次混在一起，造成“来源/进度对不上”的误判。
            if (typeof resetImportState === 'function') {
                resetImportState(false);
            }
            modal.classList.remove('hidden');
            window.MathBankModal.open(modal, {
                onEscape: () => {
                    if (window.currentPdfTaskId) cancelCurrentImportTask();
                    else closeImportModal();
                }
            });
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closeImportModal() {
            if (blockImportResetWhileSaving()) {
                return;
            }
            if (typeof performOrphanedTempCropsCleanup === 'function') {
                performOrphanedTempCropsCleanup();
            }
            const modal = document.getElementById('latexImportModal');
            window.MathBankModal.close(modal);
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
            }, 300);
        }

        // PDF & Crop Global States
        window.currentPdfFile = null;
        window.pdfPageImages = [];
        // 多文件支持：按来源文件名分别保存各文档的页面图，避免后处理的文档覆盖前面的
        window.pdfPageImagesMap = {};
        window.currentPdfTaskId = null;
        window.activeCropQuestionIndex = null;
        window.tempCroppedPathsThisSession = [];

        // Crop Selection variables
        let isDrawing = false;
        let startX = 0;
        let startY = 0;
        let rectLeft = 0;
        let rectTop = 0;
        let rectWidth = 0;
        let rectHeight = 0;
        let activePageIndex = 0;
        let baseWidth = 0;
        let baseHeight = 0;
        let zoomFactor = 1.0;

        window.zoomPdfCropIn = function() {
            zoomFactor = Math.min(3.0, zoomFactor + 0.2);
            applyZoom();
        };

        window.zoomPdfCropOut = function() {
            zoomFactor = Math.max(0.5, zoomFactor - 0.2);
            applyZoom();
        };

        window.resetPdfCropZoom = function() {
            zoomFactor = 1.0;
            applyZoom();
        };

        function applyZoom() {
            const img = document.getElementById('pdfCropActiveImage');
            const container = document.getElementById('pdfCropImageContainer');
            const zoomText = document.getElementById('pdfZoomFactorText');
            if (!img || !container || baseWidth === 0) return;
            
            const w = baseWidth * zoomFactor;
            const h = baseHeight * zoomFactor;
            
            img.style.width = `${w}px`;
            img.style.height = `${h}px`;
            img.style.maxWidth = 'none';
            img.style.maxHeight = 'none';
            
            container.style.width = `${w}px`;
            container.style.height = `${h}px`;
            
            if (zoomText) {
                zoomText.textContent = `${Math.round(zoomFactor * 100)}%`;
            }
            
            clearPdfCropSelection();
        }

        // 根据题号找到它所属文档的页面图与 taskId（多文件按 source_file；单文件全局兜底）
        function getCropDocumentForQuestion(questionIndex) {
            const q = parsedQuestionsData[questionIndex];
            const key = q && q.source_file ? q.source_file : '';
            const map = window.pdfPageImagesMap || {};
            const entry = key ? map[key] : null;
            if (entry && entry.pageImages && entry.pageImages.length > 0) {
                return entry;
            }
            // 兜底：单文件模式下题目未带 source_file，使用全局最后一份文档
            if (window.pdfPageImages && window.pdfPageImages.length > 0) {
                return { taskId: window.currentPdfTaskId, pageImages: window.pdfPageImages };
            }
            return null;
        }

        // 该题是否存在可截图的 PDF/Word 页面（控制“手动截图”按钮显隐）
        function questionHasCropPages(index) {
            const q = parsedQuestionsData[index];
            if (!q) return false;
            const key = q.source_file;
            const map = window.pdfPageImagesMap || {};
            if (key && map[key] && map[key].pageImages && map[key].pageImages.length > 0) return true;
            if (!key && window.pdfPageImages && window.pdfPageImages.length > 0) return true;
            return false;
        }

        function openPdfCropModalForQuestion(questionIndex) {
            const entry = getCropDocumentForQuestion(questionIndex);
            if (!entry) {
                showToast('该题没有可截图的 PDF/Word 页面', 'warning');
                return;
            }
            // 切换到该题所属文档的页面图与 taskId，多文件互不干扰
            window.pdfPageImages = entry.pageImages;
            if (entry.taskId) window.currentPdfTaskId = entry.taskId;
            window.activeCropQuestionIndex = questionIndex;
            activePageIndex = 0;
            zoomFactor = 1.0;
            baseWidth = 0;
            baseHeight = 0;
            window.lastCropLoadedSrc = '';
            
            // Render sidebar page thumbnails
            renderPdfPagesThumbnails();
            
            // Setup drawing listeners FIRST to avoid load race conditions
            setupPdfCropDrawListeners();
            
            // Load the first page (triggers src change and onload cleanly)
            loadPdfCropPage(0);
            
            // Show modal
            const modal = document.getElementById('pdfCropModal');
            modal.classList.remove('hidden');
            window.MathBankModal.open(modal, { onEscape: closePdfCropModal });
            setTimeout(() => {
                modal.classList.remove('opacity-0');
                modal.querySelector('div').classList.remove('scale-95');
                modal.querySelector('div').classList.add('scale-100');
            }, 50);
        }

        function closePdfCropModal() {
            const modal = document.getElementById('pdfCropModal');
            window.MathBankModal.close(modal);
            modal.classList.add('opacity-0');
            modal.querySelector('div').classList.remove('scale-100');
            modal.querySelector('div').classList.add('scale-95');
            setTimeout(() => {
                modal.classList.add('hidden');
                clearPdfCropSelection();
            }, 300);
        }

        function renderPdfPagesThumbnails() {
            const container = document.getElementById('pdfPagesThumbnailsContainer');
            container.innerHTML = '';

            console.log('[PDF预览] renderPdfPagesThumbnails 被调用, pdfPageImages 长度:', window.pdfPageImages.length);
            console.log('[PDF预览] pdfPageImages 内容:', window.pdfPageImages);

            window.pdfPageImages.forEach((url, i) => {
                const safeUrl = window.MathBankSafe.safeImageUrl(url);
                console.log(`[PDF预览] 缩略图 ${i}: 原始URL="${url}", safeUrl="${safeUrl}"`);
                if (!safeUrl) { console.warn(`[PDF预览] ⚠️ 缩略图 ${i} 被 safeImageUrl 过滤掉!`); return; }
                const thumb = document.createElement('div');
                thumb.className = `cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white ${i === activePageIndex ? 'border-brand-500 shadow-md ring-2 ring-brand-500/20' : 'border-slate-200'}`;
                thumb.innerHTML = `
                    <img src="${window.MathBankSafe.escapeAttribute(safeUrl)}" class="w-full h-full object-cover" loading="lazy" decoding="async">
                    <div class="absolute bottom-1 right-1 bg-black/60 text-white text-[8px] px-1 rounded font-bold">P${i + 1}</div>
                `;
                thumb.onclick = () => {
                    loadPdfCropPage(i);
                };
                container.appendChild(thumb);
            });
        }

        function loadPdfCropPage(pageIdx) {
            activePageIndex = pageIdx;
            
            // Update active thumbnail border class
            const thumbnails = document.getElementById('pdfPagesThumbnailsContainer').children;
            for (let i = 0; i < thumbnails.length; i++) {
                if (i === pageIdx) {
                    thumbnails[i].className = 'cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white border-brand-500 shadow-md ring-2 ring-brand-500/20';
                } else {
                    thumbnails[i].className = 'cursor-pointer border-2 rounded-lg overflow-hidden transition-all duration-200 aspect-[3/4] relative group hover:border-brand-500 bg-white border-slate-200';
                }
            }
            
            document.getElementById('pdfCropPageIndicator').textContent = `第 ${pageIdx + 1} / ${window.pdfPageImages.length} 页`;
            
            const img = document.getElementById('pdfCropActiveImage');
            const safePageUrl = window.MathBankSafe.safeImageUrl(window.pdfPageImages[pageIdx]);
            console.log(`[PDF预览] 主图 P${pageIdx + 1}: 原始URL="${window.pdfPageImages[pageIdx]}", safeUrl="${safePageUrl}"`);
            img.src = safePageUrl || '';
            // 监听 img 的 load/error 事件
            img.onload = () => console.log(`[PDF预览] ✅ 主图 P${pageIdx + 1} 加载成功`);
            img.onerror = (e) => console.error(`[PDF预览] ❌ 主图 P${pageIdx + 1} 加载失败, src="${img.src}"`, e);
            
            clearPdfCropSelection();
        }

        function setupPdfCropDrawListeners() {
            const wrapper = document.getElementById('pdfCropCanvasWrapper');
            if (!wrapper) return;
            
            // Recreate wrapper to drop old listeners clean
            const newWrapper = wrapper.cloneNode(true);
            wrapper.parentNode.replaceChild(newWrapper, wrapper);
            
            const activeWrapper = document.getElementById('pdfCropCanvasWrapper');
            const activeContainer = document.getElementById('pdfCropImageContainer');
            const activeOverlay = document.getElementById('pdfCropOverlayRect');
            const activeImg = document.getElementById('pdfCropActiveImage');
            
            // Bind trackpad pinch zoom
            activeWrapper.addEventListener('wheel', (e) => {
                if (e.ctrlKey || e.metaKey) {
                    e.preventDefault();
                    const zoomSpeed = 0.03;
                    if (e.deltaY < 0) {
                        zoomFactor = Math.min(3.0, zoomFactor + zoomSpeed);
                    } else {
                        zoomFactor = Math.max(0.5, zoomFactor - zoomSpeed);
                    }
                    applyZoom();
                }
            }, { passive: false });
            
            // Bind image onload
            activeImg.onload = function() {
                if (baseWidth === 0 || activeImg.src !== window.lastCropLoadedSrc) {
                    // Reset style to read original viewport-fitted size
                    activeImg.style.width = '';
                    activeImg.style.height = '';
                    activeImg.style.maxWidth = '';
                    activeImg.style.maxHeight = '';
                    
                    baseWidth = activeImg.clientWidth || 600;
                    baseHeight = activeImg.clientHeight || 800;
                    window.lastCropLoadedSrc = activeImg.src;
                }
                applyZoom();
            };
            
            // Bind drawing select listeners
            activeContainer.addEventListener('mousedown', (e) => {
                if (e.button !== 0) return; // Only left click
                isDrawing = true;
                
                const rect = activeContainer.getBoundingClientRect();
                startX = e.clientX - rect.left;
                startY = e.clientY - rect.top;
                
                rectLeft = startX;
                rectTop = startY;
                rectWidth = 0;
                rectHeight = 0;
                
                activeOverlay.style.left = `${rectLeft}px`;
                activeOverlay.style.top = `${rectTop}px`;
                activeOverlay.style.width = '0px';
                activeOverlay.style.height = '0px';
                activeOverlay.classList.remove('hidden');
                
                e.preventDefault();
            });
            
            window.addEventListener('mousemove', (e) => {
                if (!isDrawing) return;
                
                const rect = activeContainer.getBoundingClientRect();
                let currentX = e.clientX - rect.left;
                let currentY = e.clientY - rect.top;
                
                currentX = Math.max(0, Math.min(currentX, rect.width));
                currentY = Math.max(0, Math.min(currentY, rect.height));
                
                rectLeft = Math.min(startX, currentX);
                rectTop = Math.min(startY, currentY);
                rectWidth = Math.abs(startX - currentX);
                rectHeight = Math.abs(startY - currentY);
                
                activeOverlay.style.left = `${rectLeft}px`;
                activeOverlay.style.top = `${rectTop}px`;
                activeOverlay.style.width = `${rectWidth}px`;
                activeOverlay.style.height = `${rectHeight}px`;
            });
            
            window.addEventListener('mouseup', () => {
                if (!isDrawing) return;
                isDrawing = false;
                
                if (rectWidth > 15 && rectHeight > 15) {
                    document.getElementById('pdfCropConfirmBtn').disabled = false;
                    document.getElementById('pdfCropClearBtn').disabled = false;
                } else {
                    clearPdfCropSelection();
                }
            });
        }

        function clearPdfCropSelection() {
            const overlay = document.getElementById('pdfCropOverlayRect');
            if (overlay) {
                overlay.classList.add('hidden');
                overlay.style.width = '0px';
                overlay.style.height = '0px';
            }
            rectWidth = 0;
            rectHeight = 0;
            
            const confirmBtn = document.getElementById('pdfCropConfirmBtn');
            if (confirmBtn) confirmBtn.disabled = true;
            
            const clearBtn = document.getElementById('pdfCropClearBtn');
            if (clearBtn) clearBtn.disabled = true;
        }

        function submitPdfCropCoordinates() {
            const img = document.getElementById('pdfCropActiveImage');
            const container = document.getElementById('pdfCropImageContainer');
            
            // Adjust coords relative to base (un-zoomed) dimensions
            const containerRect = container.getBoundingClientRect();
            
            const xmin = (rectLeft / containerRect.width) * 100.0;
            const ymin = (rectTop / containerRect.height) * 100.0;
            const xmax = ((rectLeft + rectWidth) / containerRect.width) * 100.0;
            const ymax = ((rectTop + rectHeight) / containerRect.height) * 100.0;
            
            const confirmBtn = document.getElementById('pdfCropConfirmBtn');
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>正在裁剪...</span>';
            
            fetch('/api/ai/manual-crop-pdf', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Local-Token': localStorage.getItem('local_token') || ''
                },
                body: JSON.stringify({
                    task_id: window.currentPdfTaskId,
                    page_index: activePageIndex,
                    ymin: ymin,
                    xmin: xmin,
                    ymax: ymax,
                    xmax: xmax
                })
            })
            .then(r => {
                if (!r.ok) throw new Error("裁剪失败");
                return r.json();
            })
            .then(data => {
                if (data.status === 'success') {
                    showToast("裁剪并生成配图成功！已自动关联至此题卡。");
                    
                    const croppedUrl = window.MathBankSafe.safeImageUrl(data.image_path);
                    if (!croppedUrl) throw new Error('裁剪接口返回了无效的图片路径');
                    window.tempCroppedPathsThisSession.push(croppedUrl);
                    
                    const qIdx = window.activeCropQuestionIndex;
                    if (qIdx !== null && parsedQuestionsData[qIdx]) {
                        const q = parsedQuestionsData[qIdx];
                        if (!q.image_paths) q.image_paths = [];
                        
                        if (!q.image_paths.includes(croppedUrl)) {
                            q.image_paths.push(croppedUrl);
                        }
                        
                        // Append the image tag to content textarea to render in card preview
                        const card = document.getElementById(`parsed-card-${qIdx}`);
                        if (card) {
                            const textarea = card.querySelector('.card-content-textarea');
                            if (textarea) {
                                textarea.value = textarea.value.trim() + `\n\n![插图](${croppedUrl})\n\n`;
                                textarea.dispatchEvent(new Event('input'));
                            }
                        }
                        
                        const badgesContainer = document.getElementById(`card-images-badges-${qIdx}`);
                        if (badgesContainer) {
                            badgesContainer.innerHTML = '';
                            q.image_paths.forEach(path => appendSafeImageBadge(badgesContainer, path));
                        }
                    }
                    
                    closePdfCropModal();
                } else {
                    throw new Error(data.message || "裁剪错误");
                }
            })
            .catch(err => {
                console.error(err);
                showToast(`手动截图报错: ${err.message}`, 'error');
            })
            .finally(() => {
                confirmBtn.innerHTML = '<i class="fa-solid fa-crop-simple mr-1.5"></i><span>确认截取配图</span>';
            });
        }

        function performOrphanedTempCropsCleanup() {
            const tempPaths = [];
            parsedQuestionsData.forEach(q => {
                if (!q.saved && q.image_paths) {
                    q.image_paths.forEach(p => {
                        if (p.includes('/tmp/')) {
                            tempPaths.push(p);
                        }
                    });
                }
            });
            
            if (window.tempCroppedPathsThisSession && window.tempCroppedPathsThisSession.length > 0) {
                window.tempCroppedPathsThisSession.forEach(p => {
                    if (!tempPaths.includes(p)) {
                        tempPaths.push(p);
                    }
                });
            }
            
            if (tempPaths.length === 0) return;
            
            fetch('/api/ai/clear-temp-crops', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Local-Token': localStorage.getItem('local_token') || ''
                },
                body: JSON.stringify({ paths: tempPaths })
            })
            .then(r => r.json())
            .then(res => {
                console.log("[Storage Cleanup] Server cleaned temporary crops:", res);
                window.tempCroppedPathsThisSession = [];
            })
            .catch(err => {
                console.error("[Storage Cleanup] Error:", err);
            });
        }


        function setupImportFileHandlers() {
            const texDrop = document.getElementById('texDropzone');
            const texInput = document.getElementById('texFileInput');
            const texFileName = document.getElementById('texFileName');
            const texFileIcon = document.getElementById('texFileIcon');
            const latexTextarea = document.getElementById('importLatexContent');

            const imagesDrop = document.getElementById('imagesDropzone');
            const imagesInput = document.getElementById('imagesFileInput');
            const imagesCountName = document.getElementById('imagesCountName');
            const imagesFileIcon = document.getElementById('imagesFileIcon');
            const imagesListContainer = document.getElementById('importImagesList');

            // LaTeX / PDF File drag & select
            if (!texDrop || !texInput || !texFileName || !texFileIcon || !latexTextarea) {
                console.error('[Import] 试卷文件上传控件不完整，无法初始化文件选择。');
                return;
            }
            texDrop.addEventListener('click', () => texInput.click());
            texInput.addEventListener('change', (e) => {
                enqueueFiles(Array.from(e.target.files || []));
                texInput.value = '';
            });

            ['dragenter', 'dragover'].forEach(eventName => {
                texDrop.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    texDrop.classList.add('border-brand-500', 'bg-brand-50/20');
                }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                texDrop.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    texDrop.classList.remove('border-brand-500', 'bg-brand-50/20');
                }, false);
            });

            texDrop.addEventListener('drop', (e) => {
                enqueueFiles(Array.from(e.dataTransfer.files || []));
            });

            function readFileAsArrayBuffer(file) {
                if (file && typeof file.arrayBuffer === 'function') {
                    return file.arrayBuffer();
                }
                return new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result);
                    reader.onerror = () => reject(reader.error || new Error('无法读取本地文件'));
                    reader.readAsArrayBuffer(file);
                });
            }

            async function decodeTexFileLocally(file) {
                const buffer = await readFileAsArrayBuffer(file);
                const bytes = new Uint8Array(buffer || new ArrayBuffer(0));
                if (!bytes.length) throw new Error('TeX 文件为空');
                if (bytes.length > 5 * 1024 * 1024) throw new Error('TeX 文件超过 5MB 上限');

                const attempts = [];
                if ((bytes[0] === 0xFF && bytes[1] === 0xFE) || (bytes[0] === 0xFE && bytes[1] === 0xFF)) {
                    attempts.push('utf-16');
                } else if (bytes.length >= 8) {
                    let evenNuls = 0;
                    let oddNuls = 0;
                    const sampleLength = Math.min(bytes.length, 200);
                    for (let i = 0; i < sampleLength; i++) {
                        if (bytes[i] === 0) {
                            if (i % 2 === 0) evenNuls++;
                            else oddNuls++;
                        }
                    }
                    if (oddNuls > Math.max(2, evenNuls * 3)) attempts.push('utf-16le');
                    if (evenNuls > Math.max(2, oddNuls * 3)) attempts.push('utf-16be');
                }
                attempts.push('utf-8', 'gb18030');

                for (const encoding of attempts) {
                    try {
                        const decoded = new TextDecoder(encoding, {fatal: true}).decode(bytes);
                        return decoded
                            .replace(/^\uFEFF/, '')
                            .replace(/\u0000/g, '')
                            .replace(/\r\n?/g, '\n');
                    } catch (_error) {
                        // Continue through the explicit safe encoding fallbacks.
                    }
                }
                throw new Error('无法识别文件编码，请将 TeX 另存为 UTF-8 后重试');
            }

            function applyLocallyReadTex(file, source, diagnostics = null, serverTitle = '') {
                latexTextarea.value = source || '';
                latexTextarea.disabled = false;
                window.currentTexDiagnostics = diagnostics;
                const titleInput = document.getElementById('importPaperTitle');
                let autoTitle = '';
                if (!window.__isMultiFileQueueMode()) {
                    autoTitle = serverTitle || extractTitleFromLatex(source || '');
                    if (autoTitle) {
                        titleInput.value = autoTitle;
                    } else if (!titleInput.value.trim()) {
                        titleInput.value = file.name.replace(/\.[^/.]+$/, '');
                    }
                }
                return autoTitle;
            }

            function handleTexFileSelect(file, onReady) {
                if (!file) return;
                const lowerFileName = file.name.toLowerCase();
                if (!lowerFileName.endsWith('.tex') && !lowerFileName.endsWith('.pdf') && !lowerFileName.endsWith('.docx')) {
                    showToast('仅支持 .tex、.pdf 或 .docx 试卷文件。', 'warning');
                    texInput.value = '';
                    return;
                }
                window.currentTexReadToken = null;
                const texImagesSection = document.getElementById('texImagesSection');
                
                if (lowerFileName.endsWith('.docx')) {
                    window.currentDocxFile = file;
                    window.currentPdfFile = null;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-word text-blue-600 text-xl mb-1.5 animate-bounce";
                    latexTextarea.value = `[Word (.docx) 试卷已成功载入: ${file.name}]\n系统将安全提取 OMML 公式与高清插图；MathType 公式无法可靠转换时会保留原预览图并标记人工核对。`;
                    latexTextarea.disabled = true;

                    const titleInput = document.getElementById('importPaperTitle');
                    if (!window.__isMultiFileQueueMode() && !titleInput.value) {
                        titleInput.value = file.name.replace(/\.[^/.]+$/, "");
                    }

                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
                    if (texImagesSection) texImagesSection.classList.add('hidden');
                } else if (lowerFileName.endsWith('.pdf')) {
                    window.currentDocxFile = null;
                    window.currentPdfFile = file;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-pdf text-brand-500 text-xl mb-1.5 animate-bounce";
                    latexTextarea.value = `[PDF 试卷已成功载入: ${file.name}]\n总页数、高清转换与插图定位将会在点击“一键 AI 智能拆解并关联”后于后台异步执行。`;
                    latexTextarea.disabled = true;

                    const titleInput = document.getElementById('importPaperTitle');
                    if (!window.__isMultiFileQueueMode() && !titleInput.value) {
                        titleInput.value = file.name.replace(/\.[^/.]+$/, "");
                    }

                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.remove('hidden');
                    if (texImagesSection) texImagesSection.classList.add('hidden');
                } else {
                    window.currentDocxFile = null;
                    window.currentPdfFile = null;
                    window.currentTexDiagnostics = null;
                    texFileName.textContent = file.name;
                    texFileName.className = "text-xs text-brand-600 font-bold";
                    texFileIcon.className = "fa-solid fa-file-circle-check text-brand-500 text-xl mb-1.5 animate-bounce";
                    latexTextarea.disabled = true;
                    latexTextarea.value = '正在安全读取并检查 TeX 源码编码与结构...';
                    
                    const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
                    if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
                    if (texImagesSection) texImagesSection.classList.remove('hidden');
                    
                    const runBtn = document.getElementById('runParseBtn');
                    if (runBtn) {
                        runBtn.disabled = true;
                        runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>正在读取 TeX...</span>';
                    }
                    const readToken = `${Date.now()}-${Math.random()}`;
                    window.currentTexReadToken = readToken;
                    let localTitle = '';
                    decodeTexFileLocally(file)
                    .then(localSource => {
                        if (window.currentTexReadToken !== readToken) return null;
                        localTitle = applyLocallyReadTex(file, localSource);

                        const formData = new FormData();
                        formData.append('file', file);
                        const controller = typeof AbortController === 'function' ? new AbortController() : null;
                        const timeoutId = controller ? setTimeout(() => controller.abort(), 8000) : null;
                        return fetch('/api/upload/tex-source', {
                            method: 'POST',
                            headers: {'X-Local-Token': localStorage.getItem('local_token') || ''},
                            body: formData,
                            signal: controller ? controller.signal : undefined
                        })
                        .then(async response => {
                            const data = await response.json().catch(() => ({}));
                            if (!response.ok || data.status !== 'success') {
                                throw new Error(data.message || data.detail || `HTTP ${response.status}`);
                            }
                            return data;
                        })
                        .then(data => {
                            if (window.currentTexReadToken !== readToken) return;
                            const autoTitle = applyLocallyReadTex(
                                file,
                                data.source || localSource,
                                data.diagnostics || null,
                                data.title || ''
                            );
                            if (autoTitle && autoTitle !== localTitle) {
                                showToast(`已自动从 TeX 文件中读取试卷标题：${autoTitle}`);
                            }
                            const diagnostics = data.diagnostics || {};
                            if (diagnostics.encoding_fallback) {
                                showToast(`已按 ${diagnostics.encoding} 编码安全读取该 TeX 文件。`, 'info');
                            }
                            if (Array.isArray(diagnostics.warnings) && diagnostics.warnings.length) {
                                showToast(`TeX 预检发现 ${diagnostics.warnings.length} 项需留意的结构，拆分后会继续提示。`, 'warning');
                            }
                        })
                        .catch(error => {
                            if (window.currentTexReadToken !== readToken) return;
                            console.warn('TeX 后端预检不可用，已保留浏览器本地读取结果:', error);
                            window.currentTexDiagnostics = {
                                local_read_fallback: true,
                                warnings: ['后端 TeX 预检暂不可用，已使用浏览器本地读取结果']
                            };
                            showToast('TeX 文件已读取；后端预检暂不可用，不影响继续编辑。', 'warning');
                        })
                        .finally(() => {
                            if (timeoutId) clearTimeout(timeoutId);
                        });
                    })
                    .catch(error => {
                        if (window.currentTexReadToken !== readToken) return;
                        latexTextarea.value = '';
                        latexTextarea.disabled = false;
                        texInput.value = '';
                        texFileName.textContent = 'TeX 文件读取失败，请重新选择';
                        texFileName.className = 'text-xs text-red-500 font-bold';
                        showToast(`TeX 文件读取失败：${error.message}`, 'error');
                    })
                    .finally(() => {
                        if (runBtn) {
                            runBtn.disabled = false;
                            runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i><span>一键 AI 智能拆解并关联</span>';
                        }
                        // TeX 预读（异步）完成，textarea 已就绪，通知调用方继续拆解
                        if (typeof onReady === 'function') onReady();
                    });
                }
                // docx / pdf 为同步载入，函数末尾通知继续拆解（TeX 异步已在 finally 通知，避免重复）
                if (!lowerFileName.endsWith('.tex') && typeof onReady === 'function') {
                    onReady();
                }
            }

            // ===== 多文件队列（串行拆解，人工审核） =====
            // 队列项结构：{ file, name, status: 'pending'|'parsing'|'done'|'failed', error }
            // 入队时按文件名去重（同一文件名不允许重复选入队列）。
            const pendingFiles = [];
            let queueProcessing = false;

            // 多文件队列模式（≥2 个文件已入队）下隐藏「第一步：试卷标题」输入框：
            // 每道题会自动带上各自来源文件名，单标题框只会显示第一个文件名，多余且易误导。
            // 挂到 window 上，供不同作用域（handleTexFileSelect / runAIPaperParse 等）访问。
            window.__isMultiFileQueueMode = function() {
                return Array.isArray(pendingFiles) && pendingFiles.length >= 2;
            };

            function enqueueFiles(fileList) {
                if (!fileList || fileList.length === 0) return;
                let added = 0;
                let skipped = 0;
                fileList.forEach(file => {
                    const lower = (file.name || '').toLowerCase();
                    const valid = lower.endsWith('.tex') || lower.endsWith('.pdf') || lower.endsWith('.docx');
                    if (!valid) {
                        showToast(`已忽略非试卷文件：${file.name || '未知文件'}（仅支持 .tex / .pdf / .docx）`, 'warning');
                        return;
                    }
                    // 去重：队列中已有同名文件，则跳过（已导入文档的免重复拆解改由 processFileQueue 查库判断）
                    const dup = pendingFiles.some(item => item.name === file.name);
                    if (dup) {
                        skipped += 1;
                        return;
                    }
                    pendingFiles.push({ file, name: file.name, status: 'pending', error: '' });
                    added += 1;
                });
                if (skipped > 0) {
                    showToast(`已跳过 ${skipped} 个重复文件（队列中已有同名文件不会重复选入）`, 'info');
                }
                renderFileQueue();
                // 不再自动拆解：选完文件只入队并展示，等待用户点击“一键拆解”按钮
            }

            function renderFileQueue() {
                const queueEl = document.getElementById('importFileQueue');
                // 上方拖放区内的文件列表（方案B：文件名直接显示在上传区）
                renderTexFileList();
                // 下方队列区仅作为进度面板使用
                if (queueEl) {
                    if (pendingFiles.length === 0) {
                        queueEl.classList.add('hidden');
                        queueEl.innerHTML = '';
                        const texImagesSection = document.getElementById('texImagesSection');
                        if (texImagesSection) texImagesSection.classList.remove('hidden');
                    } else {
                        queueEl.classList.remove('hidden');
                        queueEl.innerHTML = '';
                    }
                }
                // 多文件队列模式（≥1 个文件已入队）下，隐藏单文件专属的配置项与底部“一键 AI 智能拆解”按钮，
                // 统一改用队列顶部的“开始拆解 N 个文件”按钮，避免误操作与视觉混乱。
                const inBatchMode = pendingFiles.length >= 1;
                document.querySelectorAll('[data-queue-collapse="1"]').forEach(el => {
                    el.classList.toggle('hidden', inBatchMode);
                });
                // 多文件队列模式（≥2 个文件）下，隐藏单文件专属的标题输入框与 TeX 配套图片区：
                // 每道题已自动带上各自来源文件名，单标题框只会显示第一份文件的名字，易误导。
                const multiFileMode = pendingFiles.length >= 2;
                const titleGroup = document.getElementById('importTitleGroup');
                if (titleGroup) titleGroup.classList.toggle('hidden', multiFileMode);
                const texImagesSection = document.getElementById('texImagesSection');
                if (texImagesSection) texImagesSection.classList.toggle('hidden', multiFileMode);
                updateFileQueueProgress();
                updateParseButtonState();
                // 同步队列快照到全局，供 saveAllParsedQuestions 跨作用域判断阶段
                window.__pendingFilesSnapshot = pendingFiles.slice();
            }

            // 把待拆解文件列表渲染到上方“上传文件”拖放区内（多文件时一目了然）
            function renderTexFileList() {
                const drop = document.getElementById('texDropzone');
                if (!drop) return;
                let listEl = document.getElementById('texFileList');
                if (!listEl) {
                    listEl = document.createElement('div');
                    listEl.id = 'texFileList';
                    listEl.className = 'w-full mt-2 space-y-1';
                    drop.appendChild(listEl);
                }
                // 队列为空，或仅剩单文件时：恢复单文件提示样式，隐藏列表（单文件由 texFileName 单独显示）
                if (pendingFiles.length <= 1) {
                    listEl.classList.add('hidden');
                    listEl.innerHTML = '';
                    const texFileName = document.getElementById('texFileName');
                    const texFileIcon = document.getElementById('texFileIcon');
                    if (texFileName) {
                        texFileName.textContent = '点击或拖放 .tex / .pdf / .docx 试卷文件';
                        texFileName.className = 'text-xs text-slate-600 font-medium';
                    }
                    if (texFileIcon) texFileIcon.className = 'fa-solid fa-file-lines text-slate-400 text-xl mb-1.5';
                    return;
                }
                listEl.classList.remove('hidden');
                listEl.innerHTML = '';

                const total = pendingFiles.length;
                const done = pendingFiles.filter(f => f.status === 'done').length;
                const failed = pendingFiles.filter(f => f.status === 'failed').length;
                const parsingIdx = pendingFiles.findIndex(f => f.status === 'parsing');
                const parsing = parsingIdx !== -1;
                // 当前正在拆解的文件序号（1-based），用于“第 X/N 个文件”
                const currentNo = parsingIdx !== -1 ? parsingIdx + 1 : (done + failed + 1);

                const header = document.createElement('div');
                header.className = 'flex items-center justify-between px-0.5 mb-1';
                header.innerHTML = `
                    <span class="text-[10px] font-bold text-slate-600">已选文件（${total}）</span>
                    <span class="text-[10px] font-bold ${parsing ? 'text-brand-600' : 'text-slate-400'}">${parsing ? '当前拆解：第 ' + currentNo + '/' + total + ' 个' : '待处理'} ${done}/${total}${failed ? ' · 失败 ' + failed : ''}</span>
                `;
                listEl.appendChild(header);

                // 细分格进度条：每格代表一个文件，已完成=绿、正在拆=蓝动效、待处理=灰
                const bar = document.createElement('div');
                bar.className = 'flex gap-0.5 mb-1.5 px-0.5';
                pendingFiles.forEach((item) => {
                    const seg = document.createElement('div');
                    seg.className = 'flex-1 h-1.5 rounded-full ' +
                        (item.status === 'done' ? 'bg-emerald-400'
                            : item.status === 'failed' ? 'bg-rose-400'
                            : item.status === 'parsing' ? 'bg-brand-500 animate-pulse'
                            : 'bg-slate-200');
                    bar.appendChild(seg);
                });
                listEl.appendChild(bar);

                const body = document.createElement('div');
                body.className = 'space-y-1 max-h-32 overflow-y-auto custom-scrollbar pr-1';
                pendingFiles.forEach((item, idx) => {
                    const isActive = idx === parsingIdx;
                    const row = document.createElement('div');
                    row.className = 'flex items-center justify-between gap-2 px-2 py-1 rounded-lg border text-[10px] ' +
                        (item.status === 'done' ? 'bg-emerald-50/70 border-emerald-200 text-emerald-700'
                            : item.status === 'failed' ? 'bg-rose-50/70 border-rose-200 text-rose-700'
                            : item.status === 'skipped' ? 'bg-amber-50/70 border-amber-200 text-amber-700'
                            : item.status === 'parsing' ? 'bg-brand-50/70 border-brand-300 text-brand-700 ring-2 ring-brand-300'
                            : 'bg-white/70 border-slate-200 text-slate-600');
                    const left = document.createElement('div');
                    left.className = 'flex items-center space-x-1.5 min-w-0';
                    const idxBadge = document.createElement('span');
                    idxBadge.className = 'shrink-0 w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold ' +
                        (item.status === 'done' ? 'bg-emerald-100 text-emerald-700'
                            : item.status === 'failed' ? 'bg-rose-100 text-rose-700'
                            : item.status === 'skipped' ? 'bg-amber-100 text-amber-700'
                            : item.status === 'parsing' ? 'bg-brand-100 text-brand-700'
                            : 'bg-slate-100 text-slate-500');
                    idxBadge.textContent = idx + 1;
                    const icon = document.createElement('i');
                    icon.className = 'fa-solid ' + (
                        item.status === 'done' ? 'fa-circle-check'
                        : item.status === 'failed' ? 'fa-circle-exclamation'
                        : item.status === 'skipped' ? 'fa-ban'
                        : item.status === 'parsing' ? 'fa-spinner fa-spin'
                        : 'fa-file-lines'
                    );
                    const nameEl = document.createElement('span');
                    nameEl.className = 'truncate font-semibold max-w-[150px]' + (item.status === 'pending' ? ' text-slate-700' : '');
                    nameEl.textContent = item.name;
                    nameEl.title = item.name;
                    left.append(idxBadge, icon, nameEl);

                    const right = document.createElement('div');
                    right.className = 'flex items-center space-x-1.5 shrink-0';
                    if (isActive) {
                        const tag = document.createElement('span');
                        tag.className = 'text-[9px] font-bold text-brand-600 bg-brand-100 rounded px-1.5 py-0.5';
                        tag.textContent = '拆解中';
                        right.appendChild(tag);
                    }
                    if (item.status === 'failed' && item.error) {
                        const errTip = document.createElement('span');
                        errTip.className = 'text-[9px] opacity-80';
                        errTip.textContent = item.error.length > 18 ? item.error.slice(0, 18) + '…' : item.error;
                        errTip.title = item.error;
                        right.appendChild(errTip);
                    }
                    if (item.status === 'failed') {
                        const retryBtn = document.createElement('button');
                        retryBtn.type = 'button';
                        retryBtn.className = 'text-[9px] font-bold text-brand-600 hover:text-brand-700 transition-colors border border-brand-300 rounded px-1.5 py-0.5';
                        retryBtn.textContent = '重试';
                        retryBtn.title = '重新拆解该失败/超时文件';
                        retryBtn.addEventListener('click', () => window.__forceReparseByName(item.name));
                        right.appendChild(retryBtn);
                    }
                    if (item.status === 'skipped') {
                        const reparseBtn = document.createElement('button');
                        reparseBtn.type = 'button';
                        reparseBtn.className = 'text-[9px] font-bold text-amber-600 hover:text-amber-700 transition-colors border border-amber-300 rounded px-1.5 py-0.5';
                        reparseBtn.textContent = '仍要拆解';
                        reparseBtn.title = '强制重新拆解该文件（用于重新查看原卷页面）';
                        reparseBtn.addEventListener('click', () => window.__forceReparseByName(item.name));
                        right.appendChild(reparseBtn);
                    }
                    // 待处理 / 失败 的项可移除（拆完的保留，方便核对）
                    if (item.status === 'pending' || item.status === 'failed') {
                        const removeBtn = document.createElement('button');
                        removeBtn.type = 'button';
                        removeBtn.className = 'text-slate-400 hover:text-red-500 transition-colors';
                        removeBtn.title = item.status === 'failed' ? '移除该失败文件' : '从队列移除';
                        removeBtn.innerHTML = '<i class="fa-solid fa-circle-xmark"></i>';
                        removeBtn.addEventListener('click', () => removeQueuedFile(idx));
                        right.appendChild(removeBtn);
                    }
                    row.append(left, right);
                    body.appendChild(row);
                });
                listEl.appendChild(body);
            }

            function removeQueuedFile(idx) {
                pendingFiles.splice(idx, 1);
                renderFileQueue();
            }

            function updateFileQueueProgress() {
                const el = document.getElementById('importQueueProgress');
                if (!el) return;
                const total = pendingFiles.length;
                const done = pendingFiles.filter(f => f.status === 'done').length;
                const failed = pendingFiles.filter(f => f.status === 'failed').length;
                const parsingIdx = pendingFiles.findIndex(f => f.status === 'parsing');
                const parsing = parsingIdx !== -1 ? pendingFiles[parsingIdx] : null;
                if (total === 0) {
                    el.classList.add('hidden');
                    el.textContent = '';
                    return;
                }
                el.classList.remove('hidden');
                if (parsing) {
                    el.innerHTML = `<i class="fa-solid fa-spinner fa-spin mr-1"></i>正在拆解第 ${parsingIdx + 1}/${total} 个：<span class="font-bold">${escapeHtml(parsing.name)}</span>`;
                } else if (done + failed + pendingFiles.filter(f => f.status === 'skipped').length === total) {
                    const skippedCnt = pendingFiles.filter(f => f.status === 'skipped').length;
                    el.innerHTML = `<i class="fa-solid fa-circle-check mr-1"></i>全部拆解完成：成功 ${done} / ${total}${failed ? ' · 失败 ' + failed : ''}${skippedCnt ? ' · 已跳过 ' + skippedCnt : ''}（可统一导入）`;
                } else {
                    el.innerHTML = `等待拆解：已拆 ${done}/${total}${failed ? ' · 失败 ' + failed : ''}，点击“开始拆解”继续`;
                }
            }

            function escapeHtml(str) {
                return String(str || '').replace(/[&<>"']/g, c => ({
                    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
                }[c]));
            }

            // 根据队列状态更新“一键拆解”按钮与“全部导入”按钮的文案与可用性。
            // 两阶段模型（方案B）：
            //   阶段一（队列还有 pending/parsing 文件）：禁用“全部导入”，引导先拆完所有文件；
            //   阶段二（所有文件都已 done 或 failed）：禁用“开始拆解”，启用“全部导入”。
            function updateParseButtonState() {
                const runBtn = document.getElementById('runParseBtn');
                const saveAllBtn = document.getElementById('saveAllParsedBtn');
                const pending = pendingFiles.filter(f => f.status === 'pending').length;
                const total = pendingFiles.length;
                const done = pendingFiles.filter(f => f.status === 'done').length;
                const failed = pendingFiles.filter(f => f.status === 'failed').length;
                const parsing = pendingFiles.some(f => f.status === 'parsing');
                // 供 saveAllParsedQuestions 判断阶段：队列是否还有未完成的文件
                window.__importQueueHasPending = (pending > 0 || parsing);

                if (total === 0) {
                    // 无队列（单文件模式）：两个按钮都恢复默认
                    if (runBtn) {
                        runBtn.disabled = false;
                        runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i><span>一键 AI 智能拆解并关联</span>';
                    }
                    if (saveAllBtn) {
                        saveAllBtn.disabled = false;
                    }
                    return;
                }

                // 阶段一：仍有文件待拆解 / 正在拆解
                if (pending > 0 || parsing) {
                    if (runBtn) {
                        // 拆解进行中（被 runAIPaperParse 置为禁用+“正在全力拆解中”）时不覆盖
                        if (!(runBtn.disabled && /正在全力拆解中/.test(runBtn.textContent))) {
                            runBtn.disabled = parsing;
                            if (parsing) {
                                runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin inline-block"></i><span>正在全力拆解中...</span>';
                            } else if (done > 0 && pending > 0) {
                                runBtn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i><span>继续拆解剩余 ${pending} 个文件</span>`;
                            } else {
                                runBtn.innerHTML = `<i class="fa-solid fa-wand-magic-sparkles"></i><span>开始拆解 ${total} 个文件</span>`;
                            }
                        }
                    }
                    // 阶段一：方案B——已有文件拆解完成时，允许先导入已拆完的题，
                    // 不必等全部文件拆完（避免个别文件卡死/失败拖累整体、已花 token 浪费）。
                    if (saveAllBtn) {
                        if (done > 0) {
                            saveAllBtn.disabled = false;
                            saveAllBtn.title = '仅导入已成功拆解的文件（其余文件拆解完后再导入）';
                        } else {
                            saveAllBtn.disabled = true;
                            saveAllBtn.title = '请先点“开始/继续拆解”把文件拆完，再统一导入';
                        }
                    }
                    return;
                }

                // 阶段二：所有文件都已 done / failed（无 pending、无 parsing）
                if (runBtn) {
                    runBtn.disabled = true;
                    runBtn.innerHTML = '<i class="fa-solid fa-circle-check"></i><span>全部文件已拆解，请导入</span>';
                }
                if (saveAllBtn) {
                    saveAllBtn.disabled = false;
                    saveAllBtn.title = '';
                }
            }

            // 服务端「已导入文档」识别：按来源文件名精确查库，命中则跳过重复拆解。
            // 网络异常时按「未导入」处理（不阻断拆解），返回 Promise<{imported,count}>。
            function checkDocumentImported(name) {
                return fetch(`/api/documents/imported?name=${encodeURIComponent(name)}`, {
                    method: 'GET',
                    headers: { 'Accept': 'application/json' }
                })
                    .then(r => r.ok ? r.json() : { imported: false, count: 0 })
                    .then(d => ({ imported: !!(d && d.imported), count: (d && d.count) || 0 }))
                    .catch(() => ({ imported: false, count: 0 }));
            }

            // 供队列卡片「仍要拆解」按钮调用：强制重新拆解某个被判定为已导入的文件。
            window.__forceReparseByName = function(name) {
                const it = pendingFiles.find(f => f.name === name);
                if (!it) return;
                it.status = 'pending';
                it.forceImport = true;
                appendImportLog(`🔁 已手动触发重新拆解「${name}」`, 'warning');
                queueProcessing = false;
                processFileQueue();
            };

            // 串行处理队列：一次只拆一个文件，拆完再取下一份
            function processFileQueue() {
                console.log('[队列] processFileQueue() 被调用', {
                    queueProcessing,
                    pendingFiles: pendingFiles.map(f => ({ name: f.name, status: f.status }))
                });
                if (queueProcessing) {
                    console.log('[队列] processFileQueue: 队列正在处理中，跳过（queueProcessing=true）');
                    return;
                }
                const next = pendingFiles.find(f => f.status === 'pending');
                if (!next) {
                    const allSettled = pendingFiles.every(f => f.status === 'done' || f.status === 'failed' || f.status === 'skipped');
                    if (allSettled && pendingFiles.length > 0) {
                        console.log('[队列] ✅ 所有文件已处理完毕:', pendingFiles.map(f => ({ name: f.name, status: f.status })));
                        const skippedCnt = pendingFiles.filter(f => f.status === 'skipped').length;
                        showToast(skippedCnt > 0 ? `拆解完成！其中 ${skippedCnt} 个文件已导入题库，已自动跳过` : '所有文件均已拆解完成！', 'success');
                        // 恢复底部按钮区
                        document.querySelectorAll('[data-queue-collapse="1"]').forEach(el => el.classList.add('hidden'));
                    }
                    queueProcessing = false;
                    return;
                }
                queueProcessing = true;
                next.status = 'parsing';
                console.log(`[队列] → 开始处理文件 "${next.name}" (${pendingFiles.filter(f => f.status !== 'pending').length + 1}/${pendingFiles.length})`);
                renderFileQueue();
                // 先查库判断该文档是否已导入：已导入（且非强制）则跳过，避免重复拆解；否则正常拆解。
                checkDocumentImported(next.name).then(info => {
                    if (info.imported && !next.forceImport) {
                        next.status = 'skipped';
                        appendImportLog(`⏭️ 已跳过「${next.name}」：该题已导入题库（共 ${info.count} 道），无需重复拆解。如需重新查看原卷，可点「仍要拆解」。`, 'warning');
                        renderFileQueue();
                        queueProcessing = false;
                        processFileQueue();
                        return;
                    }
                    // 未导入或用户强制：把当前文件载入全局状态（复用现有拆解分支）。
                    // onReady 在 PDF/Word 同步完成后、或 TeX 本地预读异步完成后触发，
                    // 确保 latexTextarea 已填充再进入拆解，避免时序问题。
                    try {
                        handleTexFileSelect(next.file, () => {
                            // 标记本次拆解归属的文件，完成后写入来源
                            window.__currentQueueFile = next;
                            // 复用现有拆解入口（appendMode=true 表示追加到审查列表）
                            runAIPaperParse(true, next.name);
                        });
                    } catch (err) {
                        next.status = 'failed';
                        next.error = err.message || '载入失败';
                        renderFileQueue();
                        queueProcessing = false;
                        processFileQueue();
                    }
                });
            }

            // 由拆解完成/失败回调调用，推进队列
            advanceQueueAfterParse = function(success, errorMsg) {
                console.log(`[队列] advanceQueueAfterParse 被调用: success=${success}, error=${errorMsg || '无'}`, {
                    pendingFiles: pendingFiles.map(f => ({ name: f.name, status: f.status })),
                    __currentQueueFile: window.__currentQueueFile ? window.__currentQueueFile.name : null,
                    queueProcessing
                });
                let cur = window.__currentQueueFile;
                // 兜底：若 __currentQueueFile 丢失（例如浏览器缓存导致旧逻辑残留、
                // 或运行中状态被异常清空），按"当前仍在 parsing 的文件"找回，
                // 避免第 N 个文件拆完后卡死、不再推第 N+1 个。
                if (!cur || pendingFiles.indexOf(cur) === -1) {
                    const stillParsing = pendingFiles.find(f => f.status === 'parsing');
                    if (stillParsing) {
                        console.log('[队列] __currentQueueFile 丢失，兜底找回:', stillParsing.name);
                        cur = stillParsing;
                        window.__currentQueueFile = stillParsing;
                    } else {
                        console.warn('[队列] ⚠️ 找不到 parsing 状态的文件！pendingFiles 状态:', pendingFiles.map(f => ({ name: f.name, status: f.status })));
                    }
                }
                if (cur && pendingFiles.indexOf(cur) !== -1) {
                    if (success) {
                        cur.status = 'done';
                        console.log(`[队列] ✅ 文件 "${cur.name}" 标记为 done`);
                    } else {
                        cur.status = 'failed';
                        cur.error = errorMsg || '拆解失败';
                        console.log(`[队列] ❌ 文件 "${cur.name}" 标记为 failed: ${errorMsg}`);
                    }
                } else if (!cur) {
                    console.error('[队列] ❌ 无法确定当前文件，队列可能已损坏');
                } else {
                    console.error('[队列] ❌ 当前文件不在 pendingFiles 中，可能被意外清理');
                }
                window.__currentQueueFile = null;
                queueProcessing = false;
                renderFileQueue();
                // 继续处理下一份
                console.log('[队列] → 调用 processFileQueue() 继续下一份');
                processFileQueue();
            }

            // 供 resetImportState 调用：清空整个文件队列与 UI
            window.__resetImportFileQueue = function() {
                pendingFiles.length = 0;
                queueProcessing = false;
                window.__currentQueueFile = null;
                renderFileQueue();
            };

            // 点击“一键拆解”按钮的统一入口：
            // - 若队列中有待拆解文件（多文件模式），启动串行队列；
            // - 否则走原单文件拆解逻辑（粘贴源码 / 单文件上传）。
            window.startImportParseClick = function() {
                const pending = pendingFiles.filter(f => f.status === 'pending').length;
                const parsing = pendingFiles.some(f => f.status === 'parsing');
                if (pendingFiles.length > 0 && (pending > 0 || parsing)) {
                    // 多文件队列模式：启动或继续拆解
                    if (!parsing) {
                        processFileQueue();
                    }
                    return;
                }
                // 单文件模式：直接调用原拆解入口
                runAIPaperParse();
            };

            // 旧版独立配图区在部分页面布局中不存在；仅在整套控件齐全时绑定，
            // 避免空元素让试卷文件选择和后续初始化一起中断。
            if (imagesDrop && imagesInput && imagesCountName && imagesFileIcon && imagesListContainer) {
                imagesDrop.addEventListener('click', () => imagesInput.click());
                imagesInput.addEventListener('change', (e) => handleImagesSelect(e.target.files));

                ['dragenter', 'dragover'].forEach(eventName => {
                    imagesDrop.addEventListener(eventName, (e) => {
                        e.preventDefault();
                        imagesDrop.classList.add('border-brand-500', 'bg-brand-50/20');
                    }, false);
                });

                ['dragleave', 'drop'].forEach(eventName => {
                    imagesDrop.addEventListener(eventName, (e) => {
                        e.preventDefault();
                        imagesDrop.classList.remove('border-brand-500', 'bg-brand-50/20');
                    }, false);
                });

                imagesDrop.addEventListener('drop', (e) => {
                    handleImagesSelect(e.dataTransfer.files);
                });
            }

            function handleImagesSelect(files) {
                if (!files || files.length === 0) return;
                const allowedExtensions = ['.png', '.jpg', '.jpeg', '.gif', '.webp'];
                let rejected = 0;
                for (let i = 0; i < files.length; i++) {
                    const f = files[i];
                    const lowerName = (f.name || '').toLowerCase();
                    const allowed = f.type.startsWith('image/') && allowedExtensions.some(ext => lowerName.endsWith(ext));
                    if (allowed && batchSelectedImages.length < 20) {
                        if (!batchSelectedImages.some(img => img.name === f.name)) {
                            batchSelectedImages.push(f);
                        }
                    } else {
                        rejected++;
                    }
                }
                renderImagesList();
                if (rejected) {
                    showToast(`有 ${rejected} 个文件不是受支持的图片，或已超过 20 张上限。`, 'warning');
                }
            }

            // Input event listener for pasted LaTeX or manual edits
            latexTextarea.addEventListener('input', () => {
                const autoTitle = extractTitleFromLatex(latexTextarea.value);
                if (autoTitle) {
                    const titleInput = document.getElementById('importPaperTitle');
                    if (titleInput.value.trim() === '') {
                        titleInput.value = autoTitle;
                        showToast(`已从输入中自动读取试卷标题: ${autoTitle}`);
                    }
                }
            });
        }

        function demoteCurrentImportLog(consoleDiv) {
            consoleDiv.querySelectorAll('[data-import-log-state="current"]').forEach(logEl => {
                logEl.dataset.importLogState = 'completed';
                logEl.className = 'text-slate-400 py-0.5';
                logEl.removeAttribute('aria-current');
            });
        }

        function appendImportLog(message, type = 'info') {
            const consoleDiv = document.getElementById('importLogsConsole');
            if (!consoleDiv) return;
            const logEl = document.createElement('div');

            const isCurrentStep = type === 'current' || type === 'success';
            if (isCurrentStep || type === 'error') {
                demoteCurrentImportLog(consoleDiv);
            }

            let colorClass = 'text-slate-500';
            if (isCurrentStep) {
                colorClass = 'text-emerald-500 font-semibold';
                logEl.dataset.importLogState = 'current';
                logEl.setAttribute('aria-current', 'step');
            } else if (type === 'completed') {
                colorClass = 'text-slate-400';
                logEl.dataset.importLogState = 'completed';
            } else if (type === 'error') {
                colorClass = 'text-red-500 font-semibold';
                logEl.dataset.importLogState = 'error';
            } else if (type === 'warning') {
                colorClass = 'text-amber-500';
                logEl.dataset.importLogState = 'warning';
            }

            logEl.className = `${colorClass} py-0.5`;
            logEl.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
            consoleDiv.appendChild(logEl);
            consoleDiv.scrollTop = consoleDiv.scrollHeight;
        }

        // ---- 拆卷步骤进度条（进度可视化 + 错误定位） ----
        function escHtml(value) {
            return String(value == null ? '' : value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;');
        }

        function resetImportSteps() {
            const container = document.getElementById('importStepsContainer');
            if (container) {
                container.innerHTML = '';
                container.classList.add('hidden');
            }
        }

        function buildImportStepHtml(step, idx, total) {
            const status = step.status || 'pending';
            let icon, ringClass, textClass;
            if (status === 'done') {
                icon = '<i class="fa-solid fa-check"></i>';
                ringClass = 'bg-emerald-500 text-white border-emerald-500';
                textClass = 'text-emerald-600';
            } else if (status === 'active') {
                icon = '<i class="fa-solid fa-circle-notch fa-spin"></i>';
                ringClass = 'bg-brand-600 text-white border-brand-600';
                textClass = 'text-brand-700 font-semibold';
            } else if (status === 'error') {
                icon = '<i class="fa-solid fa-xmark"></i>';
                ringClass = 'bg-red-500 text-white border-red-500';
                textClass = 'text-red-600 font-semibold';
            } else {
                icon = String(idx + 1);
                ringClass = 'bg-white text-slate-400 border-slate-300';
                textClass = 'text-slate-400';
            }
            const connector = idx < total - 1
                ? `<span class="absolute left-[15px] top-8 w-0.5 h-6 ${status === 'done' ? 'bg-emerald-400' : 'bg-slate-200'}"></span>`
                : '';
            const detail = step.detail
                ? `<p class="text-[10px] text-slate-500 mt-0.5 leading-snug">${escHtml(step.detail)}</p>`
                : '';
            const errorNote = status === 'error'
                ? `<p class="text-[10px] text-red-500 mt-0.5 leading-snug font-medium">此步骤出错，请检查上方错误日志</p>`
                : '';
            return (
                `<div class="relative flex items-start space-x-3 py-1" data-step-status="${escHtml(status)}">` +
                    `<div class="relative shrink-0">${connector}` +
                        `<span class="inline-flex items-center justify-center w-8 h-8 rounded-full border-2 text-xs font-bold ${ringClass}">${icon}</span>` +
                    `</div>` +
                    `<div class="pt-1 text-left">` +
                        `<p class="text-xs ${textClass}">${escHtml(step.label)}</p>` +
                        detail +
                        errorNote +
                    `</div>` +
                `</div>`
            );
        }

        function renderImportSteps(steps) {
            const container = document.getElementById('importStepsContainer');
            if (!container) return;
            if (!Array.isArray(steps) || steps.length === 0) {
                resetImportSteps();
                return;
            }
            container.classList.remove('hidden');
            const html = steps
                .map((s, i) => buildImportStepHtml(s, i, steps.length))
                .join('');
            container.innerHTML = html;
        }

        function runAIPaperParse(appendMode = false, appendSourceFile = '') {
            const titleInput = document.getElementById('importPaperTitle');
            const title = titleInput.value.trim();
            const latex = document.getElementById('importLatexContent').value.trim();

            if (!latex && !window.currentPdfFile) {
                showToast('请粘贴或上传 LaTeX 试卷内容，或拖入 PDF 文件！', 'warning');
                if (appendMode) {
                    if (typeof advanceQueueAfterParse === 'function') advanceQueueAfterParse(false, '无可用试卷内容');
                }
                return;
            }

            if (!title && !window.__isMultiFileQueueMode()) {
                if (!confirm('试卷标题为空，导入后题目来源将显示为空。\n确定继续吗？')) {
                    titleInput.focus();
                    if (appendMode) {
                        if (typeof advanceQueueAfterParse === 'function') advanceQueueAfterParse(false, '试卷标题为空');
                    }
                    return;
                }
            }

            // One generation owns task creation, polling and terminal UI. A
            // reset or a newer import makes every older callback inert.
            const importTaskGeneration = beginDocumentImportTask();
            window.__currentParseAppendMode = appendMode;
            window.__currentParseSourceFile = appendSourceFile;

            // Hide placeholder & results, show loading skeleton
            document.getElementById('importPlaceholder').classList.add('hidden');
            if (!appendMode) {
                // 单文件模式：先隐藏既有审查列表，拆完整体替换
                document.getElementById('parsedQuestionsWrapper').classList.add('hidden');
            }
            const loadingState = document.getElementById('importLoadingState');
            // 单文件模式显示加载骨架；多文件追加模式保留审查列表，进度由左侧队列显示
            if (!appendMode) {
                loadingState.classList.remove('hidden');
            }

            const loadingIcon = loadingState.querySelector('.fa-circle-notch, .fa-spinner, .fa-circle-exclamation');
            if (loadingIcon) {
                loadingIcon.className = 'fa-solid fa-circle-notch fa-spin text-brand-600 text-3xl inline-block';
            }
            
            // Clear logs
            const consoleDiv = document.getElementById('importLogsConsole');
            consoleDiv.innerHTML = '<div>[SYSTEM] 初始化 AI 拆解任务...</div>';
            
            const runBtn = document.getElementById('runParseBtn');
            runBtn.disabled = true;
            runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin inline-block"></i> <span>正在全力拆解中...</span>';

            const generateAnswersCheckbox = document.getElementById('importGenerateAnswers');
            const generateAnswers = generateAnswersCheckbox ? generateAnswersCheckbox.checked : false;

            // Handle Word (.docx) branch
            if (window.currentDocxFile) {
                document.getElementById('importLoadingText').textContent = '正在上传 Word 试卷并安全提取数学公式与高清插图...';
                appendImportLog('开始上传 Word (.docx) 试卷文件...', 'current');
                document.getElementById('importProgressBarContainer').classList.remove('hidden');
                document.getElementById('importProgressBar').style.width = '0%';
                resetImportSteps();

                const docxFormData = new FormData();
                docxFormData.append('file', window.currentDocxFile);
                docxFormData.append('generate_answers', generateAnswers ? "true" : "false");

                const separatedModeEl = document.getElementById('importSeparatedMode');
                const separatedMode = separatedModeEl ? separatedModeEl.checked : false;
                docxFormData.append('separated_mode', separatedMode ? "true" : "false");

                fetch('/api/upload/docx-task', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: docxFormData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(errData => {
                            throw new Error(errData.detail || errData.message || `HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(taskData => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    if (taskData.status === 'success') {
                        const taskId = taskData.task_id;
                        appendImportLog(`Word 任务已成功创建！任务 ID: ${taskId}，开始轮询分析切片进度...`, 'success');
                        pollPdfTaskStatus(taskId, importTaskGeneration);
                    } else {
                        throw new Error(taskData.message || '创建 Word 解析任务失败');
                    }
                })
                .catch(err => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    console.error(err);
                    appendImportLog(`Word 任务创建失败: ${err.message}`, 'error');
                    // 队列模式：任务创建失败也必须推进队列，避免永久卡死在失败的文件上
                    if (window.__currentParseAppendMode && typeof advanceQueueAfterParse === 'function') {
                        console.warn('[队列] ⚠️ Word 任务创建失败，标记文件失败并继续下一份:', err.message);
                        advanceQueueAfterParse(false, err.message || 'Word 任务创建失败');
                        return;
                    }

                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = 'Word 上传解析出错！';

                    const loadingState = document.getElementById('importLoadingState');
                    let resetBtn = document.getElementById('resetImportBtn');
                    if (!resetBtn) {
                        resetBtn = document.createElement('button');
                        resetBtn.id = 'resetImportBtn';
                        resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                        resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                        resetBtn.onclick = resetImportState;
                        loadingState.appendChild(resetBtn);
                    }
                    resetBtn.classList.remove('hidden');
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                });
                return;
            }

            // Handle PDF branch
            if (window.currentPdfFile) {
                document.getElementById('importLoadingText').textContent = '正在上传 PDF 试卷并创建处理任务...';
                appendImportLog('开始上传 PDF 试卷文件...', 'current');
                document.getElementById('importProgressBarContainer').classList.remove('hidden');
                document.getElementById('importProgressBar').style.width = '0%';
                resetImportSteps();

                const pdfFormData = new FormData();
                pdfFormData.append('file', window.currentPdfFile);
                pdfFormData.append('generate_answers', generateAnswers ? "true" : "false");

                const separatedModeEl = document.getElementById('importSeparatedMode');
                const separatedMode = separatedModeEl ? separatedModeEl.checked : false;
                pdfFormData.append('separated_mode', separatedMode ? "true" : "false");

                const pdfPageRangeInput = document.getElementById('pdfPageRange');
                const pageRange = pdfPageRangeInput ? pdfPageRangeInput.value.trim() : '';
                if (pageRange) {
                    pdfFormData.append('page_range', pageRange);
                }

                const pdfStrategyRadio = document.querySelector('input[name="pdfStrategy"]:checked');
                const pdfStrategy = pdfStrategyRadio ? pdfStrategyRadio.value : 'native_preferred';
                pdfFormData.append('pdf_strategy', pdfStrategy);

                fetch('/api/upload/pdf-task', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: pdfFormData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(errData => {
                            throw new Error(errData.detail || errData.message || `HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(taskData => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    if (taskData.status === 'success') {
                        const taskId = taskData.task_id;
                        appendImportLog(`任务已成功创建！任务 ID: ${taskId}，开始轮询后台分析进度...`, 'success');
                        pollPdfTaskStatus(taskId, importTaskGeneration);
                    } else {
                        throw new Error(taskData.message || '创建 PDF 解析任务失败');
                    }
                })
                .catch(err => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    console.error(err);
                    appendImportLog(`PDF 任务创建失败: ${err.message}`, 'error');
                    // 队列模式：任务创建失败也必须推进队列，避免永久卡死在失败的文件上
                    if (window.__currentParseAppendMode && typeof advanceQueueAfterParse === 'function') {
                        console.warn('[队列] ⚠️ PDF 任务创建失败，标记文件失败并继续下一份:', err.message);
                        advanceQueueAfterParse(false, err.message || 'PDF 任务创建失败');
                        return;
                    }

                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = 'PDF 上传解析出错！';

                    const loadingState = document.getElementById('importLoadingState');
                    let resetBtn = document.getElementById('resetImportBtn');
                    if (!resetBtn) {
                        resetBtn = document.createElement('button');
                        resetBtn.id = 'resetImportBtn';
                        resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                        resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                        resetBtn.onclick = resetImportState;
                        loadingState.appendChild(resetBtn);
                    }
                    resetBtn.classList.remove('hidden');
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                });
                return;
            }

            // Normal LaTeX branch
            document.getElementById('importLoadingText').textContent = '正在上传配套图片并整理文件名映射...';
            appendImportLog('开始检查配套图片...', 'current');
            document.getElementById('importProgressBarContainer').classList.add('hidden');

            let uploadPromise = Promise.resolve({});
            if (batchSelectedImages.length > 0) {
                appendImportLog(`检测到 ${batchSelectedImages.length} 张配图，开始多线程上传中...`, 'current');
                const imgFormData = new FormData();
                batchSelectedImages.forEach(file => {
                    imgFormData.append('files', file);
                });

                uploadPromise = fetch('/api/upload/batch', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: imgFormData
                })
                .then(r => r.json())
                .then(data => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return null;
                    if (data.status === 'success') {
                        appendImportLog('批量配图上传成功！已成功建立本地重命名路径映射。', 'success');
                        return data.mapping;
                    } else {
                        throw new Error(data.message || '图片上传失败');
                    }
                });
            } else {
                appendImportLog('无插图需关联，直接运行大文本 AI 拆解。', 'current');
            }

            uploadPromise
                .then(imageMapping => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return null;
                    let parseModelFriendly = systemPreferParseModel.includes('/') ? systemPreferParseModel.split('/').pop() : systemPreferParseModel;
                    let parseBrand = 'AI';
                    document.getElementById('importLoadingText').textContent = `${parseBrand} 正在智能分析并拆解试卷，请稍候...`;
                    appendImportLog(`正在调用 ${parseModelFriendly} 教研大模型进行试题智能分割与属性匹配...`, 'current');
                    appendImportLog('大纲映射范围：高中人教版A 必修一至选择性必修三。请耐心等候...', 'info');

                    const parseFormData = new FormData();
                    parseFormData.append('latex_content', latex);
                    parseFormData.append('paper_title', title);
                    parseFormData.append('image_mapping_json', JSON.stringify(imageMapping));
                    parseFormData.append('generate_answers', generateAnswers ? "true" : "false");

                    return fetch('/api/ai/parse-paper', {
                        method: 'POST',
                        headers: {
                            'X-Local-Token': localStorage.getItem('local_token') || ''
                        },
                        body: parseFormData
                    });
                })
                .then(r => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration) || !r) return null;
                    if (!r.ok) {
                        return r.json().then(errData => {
                            throw new Error(errData.detail || errData.message || `HTTP ${r.status}`);
                        });
                    }
                    return r.json();
                })
                .then(data => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration) || !data) return;
                    if (data.status === 'success') {
                        const appendMode = window.__currentParseAppendMode;
                        const sourceFile = window.__currentParseSourceFile;
                        let addedCount = 0;
                        if (appendMode) {
                            const res = appendParsedQuestions(data.questions, sourceFile);
                            addedCount = res.count;
                            appendImportLog(`【${sourceFile}】拆解完成，新增 ${addedCount} 道题（累计 ${parsedQuestionsData.length} 道）。`, 'success');
                        } else {
                            replaceParsedQuestions(data.questions);
                            appendImportLog(`试卷成功拆解完成！共提取出 ${parsedQuestionsData.length} 道高定数学题。`, 'success');
                        }
                        const texDiagnostics = data.tex_diagnostics || {};
                        const estimatedCount = texDiagnostics.question_count_estimate || 0;
                        const actualCount = texDiagnostics.question_count_actual || parsedQuestionsData.length;
                        if (estimatedCount) {
                            appendImportLog(`TeX 题数核对：源码约 ${estimatedCount} 题，实际拆分 ${actualCount} 题。`, estimatedCount === actualCount ? 'info' : 'warning');
                        }
                        if (texDiagnostics.math_locks_created) {
                            appendImportLog(`TeX 公式保真校验：${texDiagnostics.math_locks_restored || 0}/${texDiagnostics.math_locks_created} 个公式已按原源码恢复。`, 'info');
                        }
                        const texWarnings = Array.isArray(texDiagnostics.warnings) ? texDiagnostics.warnings : [];
                        texWarnings.forEach(message => appendImportLog(`TeX 预检：${message}`, 'warning'));
                        if (texWarnings.length && !appendMode) {
                            showToast(`TeX 拆分完成，但有 ${texWarnings.length} 项结构提示需要核对。`, 'warning');
                        }

                        if (appendMode) {
                            renderParsedQuestionsAppend(window.__currentParseStartIndex || 0);
                        } else {
                            renderParsedQuestionsList(parsedQuestionsData);
                        }

                        document.getElementById('importLoadingState').classList.add('hidden');
                        document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');

                        if (generateAnswers && !appendMode) {
                            processAsyncAnswerGeneration(parsedQuestionsData, parsedQuestionsGeneration);
                        }

                        if (appendMode) {
                            console.log('[队列] TeX 拆解完成，调用 advanceQueueAfterParse(true)');
                            if (typeof advanceQueueAfterParse === 'function') advanceQueueAfterParse(true);
                        }
                    } else {
                        const errMsg = data.message || '拆解失败';
                        if (window.__currentParseAppendMode && typeof advanceQueueAfterParse === 'function') {
                            advanceQueueAfterParse(false, errMsg);
                        }
                        throw new Error(errMsg);
                    }
                })
                .catch(err => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    console.error(err);
                    appendImportLog(`拆解出错: ${err.message}`, 'error');

                    const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                    if (loadingIcon) {
                        loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                        loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                    }
                    document.getElementById('importLoadingText').textContent = '试卷拆解中断！';

                    const loadingState = document.getElementById('importLoadingState');
                    let resetBtn = document.getElementById('resetImportBtn');
                    if (!resetBtn) {
                        resetBtn = document.createElement('button');
                        resetBtn.id = 'resetImportBtn';
                        resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                        resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                        resetBtn.onclick = resetImportState;
                        loadingState.appendChild(resetBtn);
                    }
                    resetBtn.classList.remove('hidden');

                    showToast(`试卷拆解失败: ${err.message}`, 'error');
                    if (window.__currentParseAppendMode && typeof advanceQueueAfterParse === 'function') {
                        advanceQueueAfterParse(false, err.message || '拆解失败');
                    }
                })
                .finally(() => {
                    if (!isCurrentDocumentImportTask(importTaskGeneration)) return;
                    runBtn.disabled = false;
                    runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                });
        }

        let documentImportTaskGeneration = 0;
        let activeDocumentPoll = null;

        function stopCurrentDocumentPoll() {
            if (activeDocumentPoll?.intervalId) clearInterval(activeDocumentPoll.intervalId);
            activeDocumentPoll = null;
        }

        function beginDocumentImportTask() {
            documentImportTaskGeneration += 1;
            stopCurrentDocumentPoll();
            window.currentPdfTaskId = null;
            return documentImportTaskGeneration;
        }

        function isCurrentDocumentImportTask(generation) {
            return generation === documentImportTaskGeneration;
        }

        function isCurrentDocumentPoll(identity) {
            return activeDocumentPoll === identity &&
                isCurrentDocumentImportTask(identity.generation) &&
                window.currentPdfTaskId === identity.taskId;
        }

        function finishDocumentPoll(identity) {
            if (!isCurrentDocumentPoll(identity)) return false;
            clearInterval(identity.intervalId);
            activeDocumentPoll = null;
            return true;
        }

        function cancelCurrentImportTask() {
            const taskId = window.currentPdfTaskId;
            beginDocumentImportTask();
            if (taskId) {
                fetch(`/api/tasks/${taskId}/cancel`, {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    }
                }).catch(() => {});
            }
            
            appendImportLog('[USER] 用户已手动中止当前拆分任务。', 'info');
            
            const loadingState = document.getElementById('importLoadingState');
            if (loadingState) {
                loadingState.classList.add('hidden');
            }
            
            const runBtn = document.getElementById('runParseBtn');
            if (runBtn) {
                runBtn.disabled = false;
                runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
            }
            
            if (typeof showToast === 'function') {
                showToast('已为您安全中止当前拆分流程', 'info');
            }
        }
        window.cancelCurrentImportTask = cancelCurrentImportTask;

        // 全局监听 ESC 键中止拆分流程
        window.addEventListener('keydown', function(e) {
            if (e.key === 'Escape' || e.keyCode === 27) {
                const loadingState = document.getElementById('importLoadingState');
                if (loadingState && !loadingState.classList.contains('hidden')) {
                    cancelCurrentImportTask();
                }
            }
        });

        function pollPdfTaskStatus(taskId, generation) {
            if (!isCurrentDocumentImportTask(generation)) return;
            let lastLog = '';
            const runBtn = document.getElementById('runParseBtn');

            stopCurrentDocumentPoll();
            window.currentPdfTaskId = taskId;
            const identity = { generation, taskId, intervalId: null, startedAt: Date.now() };
            activeDocumentPoll = identity;
            identity.intervalId = setInterval(() => {
                // 方案B：拆解绝对超时保护。超过 PARSE_TIMEOUT_MS 仍无 completed/error 响应，
                // 判定后端任务卡死，标记该文件失败并继续下一个，避免全盘卡死、已拆完的题也无法入库。
                const PARSE_TIMEOUT_MS = window.__PARSE_TIMEOUT_MS || (5 * 60 * 1000);
                if (Date.now() - identity.startedAt > PARSE_TIMEOUT_MS) {
                    const timedOutName = (window.__currentQueueFile && window.__currentQueueFile.name)
                        || window.__currentParseSourceFile || '';
                    console.warn('[队列] ⚠️ 拆解超时（超过 5 分钟无响应），标记失败并继续下一个文件', { name: timedOutName });
                    finishDocumentPoll(identity);
                    appendImportLog(`⏱️ 文件「${timedOutName}」拆解超时（超过 5 分钟无响应），已标记为失败。可点「重试」重新拆解，或「移除」跳过。`, 'error');
                    if (typeof advanceQueueAfterParse === 'function') {
                        advanceQueueAfterParse(false, '拆解超时（超过 5 分钟无响应）');
                    }
                    return;
                }
                fetch(`/api/tasks/${taskId}/status`)
                .then(r => {
                    if (!r.ok) throw new Error("获取任务进度失败");
                    return r.json();
                })
                .then(task => {
                    if (!isCurrentDocumentPoll(identity)) return;
                    if (task.progress !== undefined) {
                        document.getElementById('importProgressBar').style.width = `${task.progress}%`;
                    }

                    if (task.steps) {
                        renderImportSteps(task.steps);
                    }
                    
                    if (task.log && task.log !== lastLog) {
                        lastLog = task.log;
                        appendImportLog(task.log, 'current');
                        document.getElementById('importLoadingText').textContent = task.log;
                        
                        const subText = document.getElementById('importSubLoadingText');
                        if (subText) {
                            if (task.status === 'extracting_docx' || (task.log && task.log.includes('OMML'))) {
                                subText.textContent = '正在安全提取 OMML 公式与高清配图，不可靠的公式将保留预览图...';
                            } else if (task.status === 'ocr_extraction' || (task.log && task.log.includes('多模态'))) {
                                subText.textContent = '正在通过多模态视觉引擎并行转译图文与公式，请稍候...';
                            } else if (task.status === 'ai_splitting' || (task.log && task.log.includes('大模型') || task.log.includes('pdf-inspector') || task.log.includes('Word 原生'))) {
                                subText.textContent = '文本与公式已提取完毕，正在通过大模型进行题目切片与属性匹配...';
                            } else if (task.status === 'completed') {
                                subText.textContent = '拆解完成，正在呈现题目审查列表...';
                            }
                        }
                    }
                    
                    if (task.page_images && task.page_images.length > 0) {
                        window.pdfPageImages = task.page_images;
                        // 多文件：按来源文件保存页面图，避免后处理的文档覆盖前面的
                        const sourceKey = window.__currentParseSourceFile ||
                            (window.currentPdfFile && window.currentPdfFile.name) ||
                            (window.currentDocxFile && window.currentDocxFile.name) || '';
                        if (sourceKey) {
                            if (!window.pdfPageImagesMap) window.pdfPageImagesMap = {};
                            window.pdfPageImagesMap[sourceKey] = { taskId: taskId, pageImages: task.page_images };
                        }
                    }
                    
                    if (task.status === 'completed') {
                        console.log('[队列] PDF/Word 任务 completed，开始处理完成回调', { identity, appendMode: window.__currentParseAppendMode });
                        if (!finishDocumentPoll(identity)) {
                            console.warn('[队列] ⚠️ finishDocumentPoll 返回 false，generation 可能已过期，但仍尝试推进队列');
                            // 不 return——即使 generation 过期也尝试推进队列，避免卡死
                        }
                        let appendMode = window.__currentParseAppendMode;
                        const sourceFile = window.__currentParseSourceFile;
                        const isWordTask = task.document_type === 'docx';
                        const documentLabel = isWordTask ? 'Word' : 'PDF';
                        try {
                            if (appendMode) {
                                const res = appendParsedQuestions(task.data || [], sourceFile);
                                appendImportLog(`【${sourceFile}】${documentLabel} 拆解完成，新增 ${res.count} 道题（累计 ${parsedQuestionsData.length} 道）。`, 'success');
                            } else {
                                replaceParsedQuestions(task.data || []);
                                appendImportLog(`${documentLabel} 试卷分析并拆解成功！共分析出 ${parsedQuestionsData.length} 道数学题。`, 'success');
                            }
                            if (isWordTask && task.diagnostics) {
                                const report = task.diagnostics;
                                const converted = (report.omml_converted || 0) + (report.mtef_converted || 0);
                                const reviewCount = report.review_required || 0;
                                appendImportLog(`Word 提取报告：${converted} 个公式已转换，${report.images_extracted || 0} 张图片已保留，${reviewCount} 处需人工核对。`, reviewCount > 0 ? 'warning' : 'info');
                                const structuralMathType = report.mtef_structural_converted || 0;
                                const annotatedMathType = report.mtef_annotation_converted || 0;
                                const compatibleMathType = report.mtef_compatibility_converted || 0;
                                if (structuralMathType || annotatedMathType || compatibleMathType) {
                                    appendImportLog(`MathType 明细：${structuralMathType} 个按公式结构转换，${annotatedMathType} 个使用内嵌 LaTeX，${compatibleMathType} 个使用有限文本兼容。`, compatibleMathType > 0 ? 'warning' : 'info');
                                }
                                const restoredNumbers = report.numbering_converted || 0;
                                const restoredFormatting = (report.superscripts_converted || 0)
                                    + (report.subscripts_converted || 0)
                                    + (report.underlines_converted || 0)
                                    + (report.text_styles_converted || 0);
                                if (restoredNumbers || restoredFormatting) {
                                    appendImportLog(`Word 排版语义：已恢复 ${restoredNumbers} 个自动编号、${restoredFormatting} 处上下标/下划线/强调格式。`, 'info');
                                }
                                const lockedMath = report.math_locks_created || 0;
                                if (lockedMath) {
                                    appendImportLog(`公式保真校验：${report.math_locks_restored || 0}/${lockedMath} 个公式已按 Word 原文恢复，拆卷模型未直接改写最终公式。`, 'info');
                                }
                                if (reviewCount > 0 && !appendMode) {
                                    showToast(`Word 中有 ${reviewCount} 处公式、字符、图片或表格需人工核对，已保留提示标记。`, 'warning');
                                }
                            }

                            if (appendMode) {
                                renderParsedQuestionsAppend(window.__currentParseStartIndex || 0);
                            } else {
                                renderParsedQuestionsList(parsedQuestionsData);
                            }

                            document.getElementById('importLoadingState').classList.add('hidden');
                            document.getElementById('parsedQuestionsWrapper').classList.remove('hidden');

                            runBtn.disabled = false;
                            runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                        } catch (renderErr) {
                            console.error('[队列] ❌ 渲染过程异常（仍尝试推进队列）:', renderErr);
                            appendImportLog(`渲染异常: ${renderErr.message}（队列将继续推进）`, 'error');
                            // 即使渲染异常也不卡队列
                        }
                        // ★★★ 关键：无论前面是否异常，都要推进队列 ★★★
                        console.log('[队列] 准备推进队列: appendMode=', appendMode, 'typeof advanceQueueAfterParse=', typeof advanceQueueAfterParse);
                        if (appendMode) {
                            console.log('[队列] ✅ 调用 advanceQueueAfterParse(true) 推进到下一文件');
                            if (typeof advanceQueueAfterParse === 'function') advanceQueueAfterParse(true);
                        }
                    } else if (task.status === 'cancelled') {
                        if (!finishDocumentPoll(identity)) return;
                        document.getElementById('importLoadingState').classList.add('hidden');
                        runBtn.disabled = false;
                        runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                    } else if (task.status === 'error') {
                        if (!finishDocumentPoll(identity)) return;
                        appendImportLog(`分析失败: ${task.error || '未知错误'}`, 'error');
                        
                        const loadingIcon = document.querySelector('#importLoadingState .fa-spinner');
                        if (loadingIcon) {
                            loadingIcon.classList.remove('fa-spinner', 'animate-spin');
                            loadingIcon.classList.add('fa-circle-exclamation', 'text-red-500');
                        }
                        const documentLabel = task.document_type === 'docx' ? 'Word' : 'PDF';
                        document.getElementById('importLoadingText').textContent = `${documentLabel} 试卷分析中断！`;

                        const loadingState = document.getElementById('importLoadingState');
                        let resetBtn = document.getElementById('resetImportBtn');
                        if (!resetBtn) {
                            resetBtn = document.createElement('button');
                            resetBtn.id = 'resetImportBtn';
                            resetBtn.className = 'mt-4 px-6 py-2.5 rounded-xl bg-gradient-to-r from-slate-500 to-slate-600 hover:from-slate-600 hover:to-slate-700 text-white font-bold text-xs shadow-lg transition-all active:scale-95 flex items-center space-x-2';
                            resetBtn.innerHTML = '<i class="fa-solid fa-arrow-rotate-left"></i><span>重置并重新开始</span>';
                            resetBtn.onclick = resetImportState;
                            loadingState.appendChild(resetBtn);
                        }
                        resetBtn.classList.remove('hidden');
                        
                        runBtn.disabled = false;
                        runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';
                        showToast(`${documentLabel} 拆解分析失败: ${task.error || '未知错误'}`, 'error');
                        if (window.__currentParseAppendMode && typeof advanceQueueAfterParse === 'function') {
                            advanceQueueAfterParse(false, task.error || '未知错误');
                        }
                    }
                })
                .catch(err => {
                    if (isCurrentDocumentPoll(identity)) {
                        console.error('[队列] ❌ 轮询回调未捕获异常:', err);
                        // 兜底：即使回调异常也尝试推进队列，避免卡死
                        const appendMode = window.__currentParseAppendMode;
                        if (appendMode && typeof advanceQueueAfterParse === 'function') {
                            console.log('[队列] 从 catch 兜底调用 advanceQueueAfterParse(false)');
                            advanceQueueAfterParse(false, err.message || '轮询回调异常');
                        }
                    }
                });
            }, 1500);
        }

        function renderImagesList() {
            const imagesListContainer = document.getElementById('importImagesList');
            const imagesCountName = document.getElementById('imagesCountName');
            const imagesFileIcon = document.getElementById('imagesFileIcon');
            
            if (!imagesListContainer || !imagesCountName || !imagesFileIcon) return;

            imagesListContainer.innerHTML = '';
            if (batchSelectedImages.length === 0) {
                imagesListContainer.classList.add('hidden');
                imagesCountName.textContent = "点击或多选拖入试卷引用的所有图片";
                imagesCountName.className = "text-xs text-slate-600 font-medium";
                imagesFileIcon.className = "fa-solid fa-images text-slate-400 text-xl mb-1.5";
                return;
            }

            imagesListContainer.classList.remove('hidden');
            imagesCountName.textContent = `已选择 ${batchSelectedImages.length} 张图片`;
            imagesCountName.className = "text-xs text-brand-600 font-bold";
            imagesFileIcon.className = "fa-solid fa-images text-brand-500 text-xl mb-1.5 animate-pulse";

            batchSelectedImages.forEach((file, index) => {
                const item = document.createElement('div');
                item.className = "relative group flex items-center justify-between bg-white border border-slate-200 rounded-lg px-2 py-0.5 text-[10px] text-slate-600 space-x-1.5 shrink-0 max-w-[140px]";
                const name = document.createElement('span');
                name.className = 'truncate font-semibold max-w-[90px]';
                name.textContent = file.name;
                name.dataset.tooltip = file.name;
                const removeButton = document.createElement('button');
                removeButton.type = 'button';
                removeButton.className = 'text-slate-400 hover:text-red-500 transition-colors';
                removeButton.dataset.tooltip = '移除';
                removeButton.setAttribute('aria-label', `移除图片 ${file.name}`);
                const icon = document.createElement('i');
                icon.className = 'fa-solid fa-circle-xmark';
                removeButton.appendChild(icon);
                item.append(name, removeButton);
                removeButton.addEventListener('click', (e) => {
                    e.stopPropagation();
                    batchSelectedImages.splice(index, 1);
                    renderImagesList();
                });
                imagesListContainer.appendChild(item);
            });
        }

        function clearAllImportInputs() {
            if (blockImportResetWhileSaving()) {
                return false;
            }
            // 清空左侧输入栏
            const titleInput = document.getElementById('importPaperTitle');
            if (titleInput) titleInput.value = '';
            // 重置后恢复标题输入框显示（多文件模式会隐藏它）
            const titleGroupEl = document.getElementById('importTitleGroup');
            if (titleGroupEl) titleGroupEl.classList.remove('hidden');

            const latexTextarea = document.getElementById('importLatexContent');
            if (latexTextarea) {
                latexTextarea.value = '';
                latexTextarea.disabled = false;
            }

            const texFileInput = document.getElementById('texFileInput');
            if (texFileInput) texFileInput.value = '';

            const imagesFileInput = document.getElementById('imagesFileInput');
            if (imagesFileInput) imagesFileInput.value = '';

            // 重置 .tex 拖拽显示样式
            const texFileName = document.getElementById('texFileName');
            const texFileIcon = document.getElementById('texFileIcon');
            if (texFileName) {
                texFileName.textContent = "点击或拖放 .tex / .pdf / .docx 试卷文件";
                texFileName.className = "text-xs text-slate-600 font-medium";
            }
            if (texFileIcon) {
                texFileIcon.className = "fa-solid fa-file-pdf text-slate-400 text-xl mb-1.5";
            }

            // 清空批量配图
            batchSelectedImages = [];
            // 重置图片展示列表与状态
            renderImagesList();
            
            // 重置 PDF 状态
            window.currentPdfFile = null;
            window.currentDocxFile = null;
            window.currentTexDiagnostics = null;
            window.currentTexReadToken = null;
            window.pdfPageImages = [];
            window.pdfPageImagesMap = {};
            window.currentPdfTaskId = null;
            window.activeCropQuestionIndex = null;
            window.tempCroppedPathsThisSession = [];

            const pdfRange = document.getElementById('pdfPageRange');
            if (pdfRange) pdfRange.value = '';
            const pdfRangeContainer = document.getElementById('pdfPageRangeContainer');
            if (pdfRangeContainer) pdfRangeContainer.classList.add('hidden');
            const texImagesSection = document.getElementById('texImagesSection');
            if (texImagesSection) texImagesSection.classList.remove('hidden');
        }

        function resetImportState(showToastMessage = true) {
            if (blockImportResetWhileSaving()) {
                return false;
            }
            // 清空多文件队列（若存在）
            if (typeof window.__resetImportFileQueue === 'function') {
                window.__resetImportFileQueue();
            }
            beginDocumentImportTask();
            // 清空步骤进度条
            resetImportSteps();
            // 隐藏加载状态和结果视图
            document.getElementById('importLoadingState').classList.add('hidden');
            document.getElementById('parsedQuestionsWrapper').classList.add('hidden');

            // 显示占位视图
            document.getElementById('importPlaceholder').classList.remove('hidden');

            // 恢复加载状态的原始图标
            const loadingIcon = document.querySelector('#importLoadingState .fa-circle-notch, #importLoadingState .fa-spinner, #importLoadingState .fa-circle-exclamation');
            if (loadingIcon) {
                loadingIcon.className = 'fa-solid fa-circle-notch fa-spin text-brand-600 text-3xl inline-block';
            }

            // 重置加载文本
            document.getElementById('importLoadingText').textContent = '正在整理插图映射并预备上传...';

            // 隐藏重置按钮
            const resetBtn = document.getElementById('resetImportBtn');
            if (resetBtn) {
                resetBtn.classList.add('hidden');
            }

            // 清空日志控制台
            const consoleDiv = document.getElementById('importLogsConsole');
            consoleDiv.innerHTML = '<div>[SYSTEM] 准备就绪，等待上传图片...</div>';

            // 重置按钮状态
            const runBtn = document.getElementById('runParseBtn');
            runBtn.disabled = false;
            runBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles"></i> <span>一键 AI 智能拆解并关联</span>';

            // 清空解析结果数据
            replaceParsedQuestions([]);
            parsedFileGroups = [];
            if (typeof updateSelectedCount === 'function') {
                updateSelectedCount();
            }

            // 清空右侧解析题目卡片 DOM
            const container = document.getElementById('parsedCardsContainer');
            if (container) {
                container.innerHTML = '';
            }
            const countBadge = document.getElementById('parsedCountBadge');
            if (countBadge) {
                countBadge.textContent = '共 0 题';
            }

            const shouldShow = (showToastMessage === true || typeof showToastMessage !== 'boolean');
            if (shouldShow) {
                showToast('已重置，可以重新开始拆解', 'success');
            }
        }

        function appendSafeImageBadge(container, imagePath) {
            if (!container) return;
            const safePath = window.MathBankSafe.safeImageUrl(imagePath);
            if (!safePath) return;

            let filename = safePath.split('/').pop() || '题目配图';
            try {
                filename = decodeURIComponent(filename.split('?')[0]);
            } catch (error) { }
            filename = window.MathBankSafe.sanitizePlainText(filename);

            const badge = document.createElement('div');
            badge.className = 'flex items-center space-x-1 px-2 py-0.5 bg-slate-100 border rounded-full text-[9px] font-semibold text-slate-500 hover:bg-white transition-colors cursor-pointer select-none';
            const icon = document.createElement('i');
            icon.className = 'fa-solid fa-image text-slate-400';
            const label = document.createElement('span');
            label.className = 'truncate max-w-[80px]';
            label.title = filename;
            label.textContent = filename;
            badge.append(icon, label);
            container.appendChild(badge);
        }

        function renderSingleParsedCard(index) {
            const q = parsedQuestionsData[index];
            if (!q) return;
            const container = document.getElementById('parsedCardsContainer');
            const tocEl = document.getElementById('parsedTOC');
            // 渲染目录项（多文件：「（文件中文序号）题型缩写+文件内连续序号」如 （二）选1；单文件保持 选1）
            if (tocEl) {
                const tocItem = document.createElement('button');
                tocItem.type = 'button';
                tocItem.className = 'parsed-toc-item w-full flex items-center justify-center text-[10px] font-bold px-1 py-1.5 rounded-lg transition-all border select-none text-slate-500 bg-white/50 border-slate-200/60 hover:bg-brand-50 hover:text-brand-600';
                tocItem.dataset.index = index;
                tocItem.textContent = getParsedQuestionCatalogLabel(index);
                const fileInfo = getParsedQuestionFileInfo(index);
                if (q.saved) {
                    tocItem.classList.add('text-slate-400', 'opacity-60');
                    tocItem.title = `文件 ${fileInfo.fileNo} 第 ${fileInfo.fileSeq} 题（已导入）`;
                } else {
                    tocItem.title = `文件 ${fileInfo.fileNo} 第 ${fileInfo.fileSeq} 题`;
                }
                tocItem.addEventListener('click', () => scrollToParsedCard(index));
                tocEl.appendChild(tocItem);
            }
                let qTypeOptionsHtml = '';
                if (window.systemMetadata && window.systemMetadata.question_types) {
                    window.systemMetadata.question_types.forEach(item => {
                        qTypeOptionsHtml += `<option value="${window.MathBankSafe.escapeAttribute(item.value)}" ${q.question_type === item.value ? 'selected' : ''}>${window.MathBankSafe.escapeText(item.label)}</option>`;
                    });
                } else {
                    qTypeOptionsHtml = `
                        <option value="single_choice" ${q.question_type === 'single_choice' ? 'selected' : ''}>单选题</option>
                        <option value="multi_choice" ${q.question_type === 'multi_choice' ? 'selected' : ''}>多选题</option>
                        <option value="fill_in_blank" ${q.question_type === 'fill_in_blank' ? 'selected' : ''}>填空题</option>
                        <option value="detailed_answer" ${q.question_type === 'detailed_answer' ? 'selected' : ''}>解答题</option>
                    `;
                }

                let difficultyOptionsHtml = '';
                if (window.systemMetadata && window.systemMetadata.difficulties) {
                    window.systemMetadata.difficulties.forEach(item => {
                        difficultyOptionsHtml += `<option value="${window.MathBankSafe.escapeAttribute(item.value)}" ${q.difficulty === item.value ? 'selected' : ''}>${window.MathBankSafe.escapeText(item.label)}</option>`;
                    });
                } else {
                    difficultyOptionsHtml = `
                        <option value="easy_error" ${q.difficulty === 'easy_error' ? 'selected' : ''}>易错题</option>
                        <option value="normal" ${q.difficulty === 'normal' ? 'selected' : ''}>常规题</option>
                        <option value="challenge" ${q.difficulty === 'challenge' ? 'selected' : ''}>挑战题</option>
                        <option value="qiangji" ${q.difficulty === 'qiangji' ? 'selected' : ''}>强基题</option>
                    `;
                }

                const card = document.createElement('div');
                card.className = "glass-card rounded-xl p-4 space-y-3 flex flex-col relative";
                card.id = `parsed-card-${index}`;
                // 按当前审查筛选决定是否立即可见（不改动数组下标）
                if (!isCardVisibleByFilter(q)) card.classList.add('hidden');
                
                card.innerHTML = `
                    ${q.source_file ? `<div class="flex items-center space-x-1.5 mb-3 shrink-0"><i class="fa-solid fa-file-lines text-brand-500 text-[10px]"></i><span class="text-[10px] font-bold text-brand-700 bg-brand-50 border border-brand-100 rounded px-2 py-0.5 truncate max-w-full" title="${window.MathBankSafe.escapeAttribute(q.source_file)}">${window.MathBankSafe.escapeText(q.source_file)}</span></div>` : ''}
                    <!-- Card Top Configs Bar -->
                    <div class="grid grid-cols-2 sm:grid-cols-5 gap-2 border-b pb-3 shrink-0">
                        <div class="flex items-center space-x-2 select-none text-slate-700 text-xs font-bold">
                            <input type="checkbox" data-index="${index}" class="card-select-checkbox h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500 cursor-pointer transition-colors" ${q.saved ? 'disabled opacity-50' : 'checked'} onclick="event.stopPropagation()">
                            <span class="h-5 w-5 bg-brand-50 text-brand-600 rounded-full flex items-center justify-center text-[10px] font-bold border border-brand-100">${index + 1}</span>
                            <span>题型与难度</span>
                        </div>
                        <select class="card-qtype glass-select px-2 py-1.5 rounded-lg text-[10px] font-semibold">
                            ${qTypeOptionsHtml}
                        </select>
                        <select class="card-difficulty glass-select px-2 py-1.5 rounded-lg text-[10px] font-semibold">
                            ${difficultyOptionsHtml}
                        </select>
                        <input type="text" class="card-source glass-input px-2.5 py-1.5 rounded-lg text-[10px] font-semibold" placeholder="题目来源">
                        <!-- Success / Saved indicator -->
                        <div class="flex items-center justify-end">
                            <span class="card-status-badge text-[10px] font-bold px-2 py-0.5 rounded ${q.saved ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-slate-100 text-slate-500'}">${q.saved ? '已导入' : '待导入'}</span>
                        </div>
                    </div>

                    <!-- Curriculum linkage section -->
                    <div class="grid grid-cols-3 gap-2 border-b pb-3 shrink-0">
                        <select class="card-compulsory glass-select px-2 py-1.5 rounded-lg text-[10px] font-semibold">
                            <option value="">所有学段</option>
                        </select>
                        <select class="card-chapter glass-select px-2 py-1.5 rounded-lg text-[10px] font-semibold">
                            <option value="">所有章节</option>
                        </select>
                        <select class="card-knowledge glass-select px-2 py-1.5 rounded-lg text-[10px] font-semibold">
                            <option value="">所有小节</option>
                        </select>
                    </div>

                    <!-- 知识点 / 解题方法 多标签 (AI 自动打标 + 手动修正) -->
                    <div class="space-y-1 mt-2">
                        <label class="text-[9px] font-bold text-slate-500 tracking-wider">知识点 (多标签)</label>
                        <div class="card-knowledge-tags-input flex flex-wrap gap-1 items-center border border-slate-200 rounded-lg px-2 py-1.5 bg-white/50" data-field="knowledge_list">
                            <span class="card-knowledge-tags-chips flex flex-wrap gap-1"></span>
                            <input type="text" class="card-tag-add-input flex-1 min-w-[60px] bg-transparent text-[10px] outline-none" placeholder="输入后回车添加 (如: 函数单调性)">
                        </div>
                    </div>
                    <div class="space-y-1 mt-2">
                        <label class="text-[9px] font-bold text-slate-500 tracking-wider">解题方法 (多标签)</label>
                        <div class="card-solvemethod-tags-input flex flex-wrap gap-1 items-center border border-slate-200 rounded-lg px-2 py-1.5 bg-white/50" data-field="solve_method">
                            <span class="card-solvemethod-tags-chips flex flex-wrap gap-1"></span>
                            <input type="text" class="card-tag-add-input flex-1 min-w-[60px] bg-transparent text-[10px] outline-none" placeholder="输入后回车添加 (如: 数形结合)">
                        </div>
                    </div>

                    <!-- Body Content Split -->
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 flex-1">
                        <!-- Left Side: Inputs -->
                        <div class="space-y-2 flex flex-col justify-start">
                            <div class="space-y-1">
                                <label class="text-[9px] font-bold text-slate-500 tracking-wider">题干编辑</label>
                                <textarea class="card-content-textarea glass-input w-full h-24 p-2.5 rounded-lg font-mono text-[10px] resize-none custom-scrollbar"></textarea>
                            </div>
                            <div class="space-y-1">
                                <label class="text-[9px] font-bold text-slate-500 tracking-wider">答案与解析编辑</label>
                                <textarea class="card-answer-textarea glass-input w-full h-24 p-2.5 rounded-lg font-mono text-[10px] resize-none custom-scrollbar"></textarea>
                            </div>
                        </div>

                        <!-- Right Side: Realtime KaTeX Previews -->
                        <div class="border border-slate-200 rounded-xl bg-slate-50/60 p-3 overflow-y-auto max-h-56 space-y-2.5 text-xs font-serif leading-relaxed custom-scrollbar flex flex-col justify-start relative select-text">
                            <span class="absolute top-2 right-2 text-[8px] font-bold text-slate-400 bg-white/80 px-1.5 py-0.5 rounded border tracking-wider select-none">实时渲染</span>
                            <div class="card-content-preview border-b border-slate-200/60 pb-2 text-slate-800"></div>
                            <div class="card-answer-preview text-slate-700"></div>
                        </div>
                    </div>

                    <!-- Card Actions Footer -->
                    <div class="flex justify-between items-center border-t border-slate-100 pt-3 shrink-0">
                        <div class="flex flex-wrap gap-1.5 items-center max-w-[70%]" id="card-images-badges-${index}">
                            <!-- Thumbnail labels of images selected -->
                        </div>
                        <div class="flex items-center space-x-2">
                            ${questionHasCropPages(index) ? `
                                <button onclick="openPdfCropModalForQuestion(${index})" class="glass-btn text-amber-700 font-bold px-3 py-1.5 rounded-lg text-[10px] flex items-center space-x-1" title="查看 PDF 页面并拖拽框选截图">
                                    <i class="fa-solid fa-scissors"></i>
                                    <span>手动截图</span>
                                </button>
                            ` : ''}
                            <button onclick="generateSingleAnswer(${index})" class="card-solve-btn glass-btn text-indigo-700 font-bold px-3 py-1.5 rounded-lg text-[10px] flex items-center space-x-1 shrink-0" title="对本题单独调用 AI 生成详细解答与解析">
                                <i class="fa-solid fa-wand-magic-sparkles text-indigo-500"></i>
                                <span>${q.answer_markdown ? '重生成解析' : 'AI 生成解析'}</span>
                            </button>
                            <button onclick="saveParsedQuestion(${index})" class="card-save-btn px-4 py-1.5 rounded-lg text-[10px] flex items-center space-x-1 shrink-0 ${q.saved ? 'bg-emerald-50 text-emerald-700 font-bold border border-emerald-300 hover:bg-emerald-100' : 'glass-btn text-brand-700 font-bold'}">
                                <i class="fa-solid ${q.saved ? 'fa-rotate-right' : 'fa-file-arrow-up'}"></i>
                                <span>${q.saved ? '再次导入' : '导入此题'}</span>
                            </button>
                        </div>
                    </div>
                `;

                // Never interpolate AI/import values into attributes or textarea
                // HTML. Property assignment preserves LaTeX verbatim and prevents
                // attribute/textarea breakout payloads.
                card.querySelector('.card-source').value = window.MathBankSafe.sanitizePlainText(q.source || '');
                card.querySelector('.card-content-textarea').value = String(q.content || '');
                card.querySelector('.card-answer-textarea').value = String(q.answer_markdown || '');

                container.appendChild(card);
                setupCardCategoryLinkage(card, q);

                // 初始化知识点 / 解题方法 多标签输入
                setupCardTagInput(card, 'knowledge_list', q.knowledge_list || '');
                setupCardTagInput(card, 'solve_method', q.solve_method || '');

                // 分离式拆解缺口提示：若该题解析缺失（source 含 [缺解析] 标记），高亮警告
                if (q.source && String(q.source).includes('[缺解析]')) {
                    const warnBar = document.createElement('div');
                    warnBar.className = 'mt-2 px-2.5 py-1.5 rounded-lg bg-rose-50 border border-rose-200 text-rose-700 text-[10px] font-semibold flex items-center space-x-1.5';
                    warnBar.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i><span>该题在解析区未找到对应段落（解析缺失），导入前请手动补全答案。</span>';
                    const cardBody = card.querySelector('.card-body') || card.querySelector('.card-content-preview');
                    if (cardBody && cardBody.parentNode) {
                        cardBody.parentNode.insertBefore(warnBar, cardBody.nextSibling);
                    } else {
                        card.appendChild(warnBar);
                    }
                }

                // Populate image badges
                const badgesContainer = document.getElementById(`card-images-badges-${index}`);
                const mappedImgs = Array.isArray(q.image_paths)
                    ? q.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                    : [];
                q.image_paths = Array.from(new Set(mappedImgs));
                q.image_paths.forEach(path => appendSafeImageBadge(badgesContainer, path));

                // Set up checkbox listener
                const selectCb = card.querySelector('.card-select-checkbox');
                selectCb.addEventListener('change', () => {
                    if (typeof updateSelectedCount === 'function') updateSelectedCount();
                });

                // Set up preview
                const textInput = card.querySelector('.card-content-textarea');
                const ansInput = card.querySelector('.card-answer-textarea');
                
                const triggerPreview = () => {
                    renderParsedCardPreview(card, textInput.value, ansInput.value);
                };

                textInput.addEventListener('input', debounce(triggerPreview, 200));
                ansInput.addEventListener('input', debounce(triggerPreview, 200));

                triggerPreview();

            if (typeof updateSelectedCount === 'function') updateSelectedCount();
        }

        // 在审查列表里渲染一个“文件分组头”，例如：📄 文件 2/5：xxx.pdf（18题）
        function renderFileGroupHeader(groupIndex, group) {
            const container = document.getElementById('parsedCardsContainer');
            if (!container || !group) return;
            const total = parsedFileGroups.length;
            const header = document.createElement('div');
            header.className = 'flex items-center justify-between gap-2 mt-5 mb-2 px-3 py-2 rounded-xl bg-brand-50/80 border border-brand-200/80 sticky top-0 z-10 backdrop-blur-sm';
            header.dataset.groupHeader = groupIndex;
            header.innerHTML = `
                <div class="flex items-center space-x-2 min-w-0">
                    <i class="fa-solid fa-file-lines text-brand-600 text-sm shrink-0"></i>
                    <span class="text-[11px] font-bold text-brand-800 truncate" title="${window.MathBankSafe.escapeAttribute(group.name)}">文件 ${groupIndex + 1}/${total}：${window.MathBankSafe.escapeText(group.name)}</span>
                </div>
                <span data-group-visible class="text-[10px] font-bold text-brand-600 bg-white/70 border border-brand-100 rounded-full px-2 py-0.5 shrink-0">${group.count} 题</span>
            `;
            container.appendChild(header);
        }

        function renderParsedQuestionsList(questions) {
            const container = document.getElementById('parsedCardsContainer');
            const tocEl = document.getElementById('parsedTOC');
            container.innerHTML = '';
            if (tocEl) tocEl.innerHTML = '';
            document.getElementById('parsedCountBadge').textContent = `共 ${questions.length} 题`;

            if (questions.length === 0) {
                container.innerHTML = '<div class="p-12 text-center text-slate-400 text-xs">AI 未能拆解出任何有效的题目，请检查 LaTeX 格式是否规整。</div>';
                if (tocEl) tocEl.classList.add('hidden');
                if (typeof updateSelectedCount === 'function') updateSelectedCount();
                return;
            }

            if (tocEl) tocEl.classList.remove('hidden');
            // 多文件批量：在每个文件分组的第一题前插入分组头
            const groupStarts = new Set(parsedFileGroups.map(g => g.startIndex));
            questions.forEach((q, index) => {
                const gi = parsedFileGroups.findIndex(g => g.startIndex === index);
                if (gi !== -1) renderFileGroupHeader(gi, parsedFileGroups[gi]);
                renderSingleParsedCard(index);
            });
            if (typeof updateSelectedCount === 'function') updateSelectedCount();
            initParsedTOCScrollSpy();
            applyReviewFilter();
        }

        // 多文件模式：增量渲染新追加的卡片（不清空既有列表，仅渲染 startIndex 之后的新题）
        function renderParsedQuestionsAppend(startIndex) {
            const container = document.getElementById('parsedCardsContainer');
            const tocEl = document.getElementById('parsedTOC');
            if (!container) return;
            document.getElementById('parsedCountBadge').textContent = `共 ${parsedQuestionsData.length} 题`;
            if (tocEl && parsedQuestionsData.length > 0) tocEl.classList.remove('hidden');
            for (let index = startIndex; index < parsedQuestionsData.length; index++) {
                // 若该下标正是一个新文件分组的起点，先插入分组头
                const gi = parsedFileGroups.findIndex(g => g.startIndex === index);
                if (gi !== -1) renderFileGroupHeader(gi, parsedFileGroups[gi]);
                renderSingleParsedCard(index);
            }
            if (typeof updateSelectedCount === 'function') updateSelectedCount();
            initParsedTOCScrollSpy();
            applyReviewFilter();
        }

        // 点击目录项：平滑滚动到对应卡片
        function scrollToParsedCard(index) {
            const card = document.getElementById(`parsed-card-${index}`);
            const scrollBox = document.getElementById('parsedCardsContainer');
            if (!card || !scrollBox) return;
            // 用 getBoundingClientRect 精确计算相对偏移，避免 offsetParent 层级不一致导致算错
            const cardRect = card.getBoundingClientRect();
            const boxRect = scrollBox.getBoundingClientRect();
            const delta = cardRect.top - boxRect.top; // 卡片顶部相对滚动容器顶部的距离
            scrollBox.scrollTo({ top: scrollBox.scrollTop + delta - 8, behavior: 'smooth' });
            // 立即高亮被点击项，并让其在目录栏可视区内
            highlightParsedTOCItem(index);
        }

        // 高亮指定目录项
        function highlightParsedTOCItem(index) {
            const tocEl = document.getElementById('parsedTOC');
            if (!tocEl) return;
            const items = tocEl.querySelectorAll('.parsed-toc-item');
            let activeItem = null;
            items.forEach(item => {
                const active = Number(item.dataset.index) === index;
                item.classList.toggle('bg-brand-600', active);
                item.classList.toggle('text-white', active);
                item.classList.toggle('border-brand-600', active);
                item.classList.toggle('bg-white/50', !active);
                item.classList.toggle('border-slate-200/60', !active);
                item.classList.toggle('text-slate-500', !active);
                if (active) activeItem = item;
            });
            // 让高亮项在目录栏自身可视区内可见（目录可滚动时长列表也跟焦）
            if (activeItem) {
                const itemRect = activeItem.getBoundingClientRect();
                const tocRect = tocEl.getBoundingClientRect();
                if (itemRect.top < tocRect.top || itemRect.bottom > tocRect.bottom) {
                    activeItem.scrollIntoView({ block: 'nearest' });
                }
            }
        }

        // 滚动跟随：用 IntersectionObserver 判断当前最靠近视口顶部的卡片
        let _parsedTOCObserver = null;
        function initParsedTOCScrollSpy() {
            const scrollBox = document.getElementById('parsedCardsContainer');
            const tocEl = document.getElementById('parsedTOC');
            if (!scrollBox || !tocEl) return;

            // 清理旧观察者
            if (_parsedTOCObserver) {
                _parsedTOCObserver.disconnect();
                _parsedTOCObserver = null;
            }

            const cards = scrollBox.querySelectorAll('[id^="parsed-card-"]');
            if (!cards.length) return;

            let currentIndex = Number(tocEl.querySelector('.parsed-toc-item.active')?.dataset.index || 0);

            _parsedTOCObserver = new IntersectionObserver((entries) => {
                // 找到顶部附近、可见比例最高的卡片
                let best = null;
                entries.forEach(entry => {
                    if (entry.isIntersecting && (!best || entry.boundingClientRect.top < best.boundingClientRect.top)) {
                        best = entry;
                    }
                });
                if (!best) return;
                const idxAttr = best.target.id.replace('parsed-card-', '');
                const idx = Number(idxAttr);
                if (!isNaN(idx) && idx !== currentIndex) {
                    currentIndex = idx;
                    highlightParsedTOCItem(idx);
                }
            }, {
                root: scrollBox,
                // 触发带设在容器顶部约 1/3 处：卡片顶部越过这条线即视为"当前题"
                rootMargin: '0px 0px -66% 0px',
                threshold: 0
            });

            cards.forEach(card => _parsedTOCObserver.observe(card));
        }

        function setupCardCategoryLinkage(card, q) {
            const compSelect = card.querySelector('.card-compulsory');
            const chapSelect = card.querySelector('.card-chapter');
            const knowSelect = card.querySelector('.card-knowledge');

            compSelect.innerHTML = '<option value="">-- 选择学段 --</option>';
            Object.keys(categoryTree).forEach(c => {
                const opt = document.createElement('option');
                opt.value = c;
                opt.textContent = c;
                if (c === q.category_compulsory) opt.selected = true;
                compSelect.appendChild(opt);
            });

            const updateChapters = () => {
                const comp = compSelect.value;
                chapSelect.innerHTML = '<option value="">-- 选择章节 --</option>';
                knowSelect.innerHTML = '<option value="">-- 先选择章节 --</option>';
                knowSelect.disabled = true;

                if (comp && categoryTree[comp]) {
                    chapSelect.disabled = false;
                    Object.keys(categoryTree[comp]).forEach(ch => {
                        const opt = document.createElement('option');
                        opt.value = ch;
                        opt.textContent = ch;
                        if (ch === q.category_chapter) opt.selected = true;
                        chapSelect.appendChild(opt);
                    });
                } else {
                    chapSelect.disabled = true;
                }
            };

            const updateKnowledge = () => {
                const comp = compSelect.value;
                const chap = chapSelect.value;
                knowSelect.innerHTML = '<option value="">-- 选择小节 (默认整章) --</option>';

                if (comp && chap && categoryTree[comp][chap]) {
                    knowSelect.disabled = false;
                    categoryTree[comp][chap].forEach(k => {
                        const opt = document.createElement('option');
                        opt.value = k;
                        opt.textContent = k;
                        if (k === q.category_knowledge) opt.selected = true;
                        knowSelect.appendChild(opt);
                    });
                } else {
                    knowSelect.disabled = true;
                }
            };

            compSelect.addEventListener('change', () => {
                updateChapters();
                updateKnowledge();
            });

            chapSelect.addEventListener('change', () => {
                updateKnowledge();
            });

            updateChapters();
            updateKnowledge();
        }

        // 初始化拆解卡片的多标签输入 (知识点 / 解题方法)
        function setupCardTagInput(card, field, initialValue) {
            const container = card.querySelector(`.card-${field === 'knowledge_list' ? 'knowledge' : 'solvemethod'}-tags-input`);
            if (!container) return;
            const chipsSpan = container.querySelector(`.card-${field === 'knowledge_list' ? 'knowledge' : 'solvemethod'}-tags-chips`);
            const input = container.querySelector('.card-tag-add-input');

            const currentTags = [];
            const addTag = (raw) => {
                const tag = (raw || '').trim();
                if (!tag) return;
                if (currentTags.includes(tag)) { input.value = ''; return; }
                currentTags.push(tag);
                renderChips();
            };
            const renderChips = () => {
                chipsSpan.innerHTML = '';
                currentTags.forEach((tag, idx) => {
                    const chip = document.createElement('span');
                    chip.className = 'inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-brand-50 text-brand-700 text-[10px] font-semibold';
                    chip.textContent = tag;
                    const x = document.createElement('span');
                    x.className = 'cursor-pointer text-brand-400 hover:text-brand-700';
                    x.textContent = '×';
                    x.addEventListener('click', () => {
                        currentTags.splice(idx, 1);
                        renderChips();
                    });
                    chip.appendChild(x);
                    chipsSpan.appendChild(chip);
                });
            };

            // 解析初始值 (逗号/顿号/分号分隔)
            String(initialValue || '').split(/[,，;；\n]+/).forEach(t => { if (t.trim()) addTag(t); });

            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ',' || e.key === '，') {
                    e.preventDefault();
                    addTag(input.value);
                    input.value = '';
                } else if (e.key === 'Backspace' && !input.value && currentTags.length) {
                    currentTags.pop();
                    renderChips();
                }
            });
            input.addEventListener('blur', () => { if (input.value.trim()) { addTag(input.value); input.value = ''; } });

            // 暴露取值方法供保存时调用
            container._getTags = () => currentTags.join(',');
        }

        // 编辑弹窗的多标签输入初始化 (知识点 / 解题方法)
        function setupEditTagInput(containerId, chipsId, inputId, initialValue) {
            const container = document.getElementById(containerId);
            const chipsSpan = document.getElementById(chipsId);
            const input = document.getElementById(inputId);
            if (!container || !chipsSpan || !input) return;

            // 用容器 id 推导全局存储键
            const globalKey = containerId === 'editKnowledgeTags' ? '_editKnowledgeTags' : '_editSolveMethodTags';
            const currentTags = [];
            window[globalKey] = currentTags;

            const renderChips = () => {
                chipsSpan.innerHTML = '';
                currentTags.forEach((tag, idx) => {
                    const chip = document.createElement('span');
                    chip.className = 'inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-brand-50 text-brand-700 text-[10px] font-semibold';
                    chip.textContent = tag;
                    const x = document.createElement('span');
                    x.className = 'cursor-pointer text-brand-400 hover:text-brand-700';
                    x.textContent = '×';
                    x.addEventListener('click', () => {
                        currentTags.splice(idx, 1);
                        renderChips();
                    });
                    chip.appendChild(x);
                    chipsSpan.appendChild(chip);
                });
            };
            const addTag = (raw) => {
                const tag = (raw || '').trim();
                if (!tag || currentTags.includes(tag)) return;
                currentTags.push(tag);
                renderChips();
            };

            String(initialValue || '').split(/[,，;；\n]+/).forEach(t => { if (t.trim()) addTag(t); });

            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ',' || e.key === '，') {
                    e.preventDefault();
                    addTag(input.value);
                    input.value = '';
                } else if (e.key === 'Backspace' && !input.value && currentTags.length) {
                    currentTags.pop();
                    renderChips();
                }
            });
            input.addEventListener('blur', () => { if (input.value.trim()) { addTag(input.value); input.value = ''; } });
        }

        function renderParsedCardPreview(card, contentText, answerText) {
            const contentPrev = card.querySelector('.card-content-preview');
            const answerPrev = card.querySelector('.card-answer-preview');
            
            // Extract card index to find its image_paths dynamically
            const indexStr = card.id ? card.id.replace('parsed-card-', '') : '';
            const index = indexStr ? parseInt(indexStr) : null;
            const q = (index !== null && !isNaN(index)) ? parsedQuestionsData[index] : null;
            
            // For content
            if (!contentText.trim()) {
                contentPrev.innerHTML = '<span class="text-slate-400 italic text-[10px]">题干预览将在此实时渲染...</span>';
            } else {
                try {
                    let processedContent = contentText;
                    if (typeof window.cleanChoiceStemParentheses === 'function') {
                        processedContent = window.cleanChoiceStemParentheses(processedContent);
                    }
                    let html = parseMarkdownWithMath(processedContent);
                    
                    // Automatically append associated image thumbnails to preview if not already rendered in markdown HTML
                    if (q && q.image_paths && q.image_paths.length > 0) {
                        let hasUnrenderedImage = false;
                        let imgHtml = '<div class="flex flex-wrap gap-2 mt-3 pt-2.5 border-t border-dashed border-slate-200/60">';
                        q.image_paths.forEach(p => {
                            const safePath = window.MathBankSafe.safeImageUrl(p);
                            if (safePath && !html.includes(safePath)) {
                                hasUnrenderedImage = true;
                                imgHtml += `
                                    <div class="relative group border border-slate-200 rounded-lg overflow-hidden bg-white max-w-[120px] aspect-[4/3] flex items-center justify-center shadow-sm hover:shadow-sm transition-all duration-300">
                                        <img src="${window.MathBankSafe.escapeAttribute(safePath)}" class="max-h-full max-w-full object-contain cursor-zoom-in hover:scale-105 transition-transform duration-300" data-safe-image-open="true" title="点击在新标签页中查看大图">
                                    </div>`;
                            }
                        });
                        imgHtml += '</div>';
                        if (hasUnrenderedImage) {
                            html += imgHtml;
                        }
                    }
                    
                    contentPrev.innerHTML = window.MathBankSafe.sanitizeRichHtml(html);
                    renderMathInElement(contentPrev, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                    if (typeof window.adaptChoicesGridLayout === 'function') {
                        window.adaptChoicesGridLayout(contentPrev);
                    }
                } catch(e) {
                    contentPrev.textContent = contentText;
                }
            }

            // For answer
            if (!answerText.trim()) {
                answerPrev.innerHTML = '<span class="text-slate-400 italic text-[10px]">解析预览将在此实时渲染...</span>';
            } else {
                try {
                    answerPrev.innerHTML = parseMarkdownWithMath(answerText);
                    renderMathInElement(answerPrev, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        throwOnError: false
                    });
                } catch(e) {
                    answerPrev.textContent = answerText;
                }
            }
        }

        function saveParsedQuestion(index) {
            const q = parsedQuestionsData[index];
            if (!q) return Promise.resolve(true);
            const existingSave = parsedQuestionSaveInFlight.get(q);
            if (existingSave) return existingSave;

            const saveGeneration = parsedQuestionsGeneration;

            const card = document.getElementById(`parsed-card-${index}`);
            if (!card) return Promise.reject(new Error('Card element not found'));

            const content = card.querySelector('.card-content-textarea').value.trim();
            const answer_markdown = card.querySelector('.card-answer-textarea').value.trim();
            const question_type = card.querySelector('.card-qtype').value;
            const difficulty = card.querySelector('.card-difficulty').value;
            const source = card.querySelector('.card-source').value.trim();
            
            const category_compulsory = card.querySelector('.card-compulsory').value;
            const category_chapter = card.querySelector('.card-chapter').value;
            const category_knowledge = card.querySelector('.card-knowledge').value;

            const knowledgeListEl = card.querySelector('.card-knowledge-tags-input');
            const solveMethodEl = card.querySelector('.card-solvemethod-tags-input');
            const knowledge_list = knowledgeListEl && knowledgeListEl._getTags ? knowledgeListEl._getTags() : '';
            const solve_method = solveMethodEl && solveMethodEl._getTags ? solveMethodEl._getTags() : '';

            if (!content) {
                showToast(`第 ${index + 1} 题的题干内容不能为空！`, 'warning');
                return Promise.reject(new Error('Content empty'));
            }
            if (!category_compulsory || !category_chapter) {
                showToast(`请选择第 ${index + 1} 题的学段与所属章节！`, 'warning');
                
                // Auto-scroll to the missing classification select inside this specific parsed card!
                const compSelect = card.querySelector('.card-compulsory');
                const chapSelect = card.querySelector('.card-chapter');
                const targetSelect = !category_compulsory ? compSelect : chapSelect;
                
                if (targetSelect) {
                    targetSelect.scrollIntoView({ behavior: 'smooth', block: 'center' });
                    targetSelect.classList.remove('border-slate-200');
                    targetSelect.classList.add('ring-2', 'ring-red-400', 'border-red-400');
                    setTimeout(() => {
                        targetSelect.classList.remove('ring-2', 'ring-red-400', 'border-red-400');
                        targetSelect.classList.add('border-slate-200');
                    }, 2500);
                    targetSelect.focus();
                }
                
                return Promise.reject(new Error('Curriculum empty'));
            }

            const saveBtn = card.querySelector('.card-save-btn');
            saveBtn.disabled = true;
            saveBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>保存中...</span>';

            const formData = new FormData();
            formData.append('content', content);
            formData.append('question_type', question_type);
            formData.append('category_compulsory', category_compulsory);
            formData.append('category_chapter', category_chapter);
            formData.append('category_knowledge', category_knowledge);
            formData.append('knowledge_list', knowledge_list);
            formData.append('solve_method', solve_method);
            formData.append('difficulty', difficulty);
            formData.append('source', source);
            formData.append('answer_markdown', answer_markdown);
            const safeImagePaths = Array.isArray(q.image_paths)
                ? q.image_paths.map(path => window.MathBankSafe.safeImageUrl(path)).filter(Boolean)
                : [];
            formData.append('image_paths', JSON.stringify(Array.from(new Set(safeImagePaths))));

            const saveOperation = fetch('/api/questions', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'duplicate_warning') {
                    // 查重命中：标记卡片为疑似重复，默认不入库，提供「仍导入」入口
                    if (!isParsedQuestionSaveContextCurrent(saveGeneration, index, q)) {
                        return true;
                    }
                    card.classList.add('ring-2', 'ring-amber-400', 'border-amber-400');
                    const warnTip = document.createElement('div');
                    warnTip.className = 'mt-2 px-2.5 py-1.5 rounded-lg bg-amber-50 border border-amber-200 text-amber-700 text-[10px] font-semibold flex items-center justify-between';
                    warnTip.innerHTML = `<span><i class="fa-solid fa-triangle-exclamation mr-1"></i>疑似重复（相似度 ${Math.round((data.similarity || 0) * 100)}%）</span>`;
                    const forceBtn = document.createElement('button');
                    forceBtn.className = 'ml-2 px-2 py-0.5 rounded bg-amber-600 text-white text-[10px] font-bold hover:bg-amber-700';
                    forceBtn.textContent = '仍导入';
                    forceBtn.onclick = () => {
                        const fd = new FormData();
                        fd.append('content', content);
                        fd.append('question_type', question_type);
                        fd.append('category_compulsory', category_compulsory);
                        fd.append('category_chapter', category_chapter);
                        fd.append('category_knowledge', category_knowledge);
                        fd.append('knowledge_list', knowledge_list);
                        fd.append('solve_method', solve_method);
                        fd.append('difficulty', difficulty);
                        fd.append('source', source);
                        fd.append('answer_markdown', answer_markdown);
                        fd.append('image_paths', JSON.stringify(Array.from(new Set(safeImagePaths))));
                        fd.append('force', '1');
                        forceBtn.disabled = true;
                        forceBtn.textContent = '导入中…';
                        fetch('/api/questions', { method: 'POST', body: fd })
                            .then(r => r.json())
                            .then(d2 => {
                                if (d2.status === 'success') {
                                    warnTip.remove();
                                    card.classList.remove('ring-2', 'ring-amber-400', 'border-amber-400');
                                    q.saved = true;
                                    const statusBadge = card.querySelector('.card-status-badge');
                                    if (statusBadge) { statusBadge.textContent = '已导入'; statusBadge.className = 'card-status-badge text-[10px] font-bold px-2 py-0.5 rounded bg-green-50 text-green-700 border border-green-200'; }
                                    // 同步目录项：已导入灰显
                                    const tocItem = document.querySelector(`#parsedTOC .parsed-toc-item[data-index="${index}"]`);
                                    if (tocItem) { tocItem.classList.add('text-slate-400', 'opacity-60'); tocItem.title = `第 ${index + 1} 题（已导入）`; }
                                    showToast(`第 ${index + 1} 题已强制导入`);
                                    loadCategories(); loadQuestions();
                                } else {
                                    forceBtn.disabled = false;
                                    forceBtn.textContent = '仍导入';
                                    showToast(`强制导入失败: ${d2.message || ''}`, 'error');
                                }
                            });
                    };
                    warnTip.appendChild(forceBtn);
                    const cardBody = card.querySelector('.card-body') || card;
                    cardBody.appendChild(warnTip);
                    saveBtn.disabled = false;
                    saveBtn.innerHTML = '<i class="fa-solid fa-file-arrow-up"></i> <span>导入此题</span>';
                    if (typeof updateSelectedCount === 'function') updateSelectedCount();
                    return false;
                }
                if (data.status === 'success') {
                    // The request may finish after a new paper has replaced this
                    // index. The backend save remains valid, but stale callbacks
                    // must never mutate the new import session or its card.
                    if (!isParsedQuestionSaveContextCurrent(saveGeneration, index, q)) {
                        return true;
                    }
                    q.saved = true;
                    
                    const statusBadge = card.querySelector('.card-status-badge');
                    statusBadge.textContent = '已导入';
                    statusBadge.className = 'card-status-badge text-[10px] font-bold px-2 py-0.5 rounded bg-green-50 text-green-700 border border-green-200 animate-pulse';

                    // 同步目录项：已导入灰显
                    const tocItem = document.querySelector(`#parsedTOC .parsed-toc-item[data-index="${index}"]`);
                    if (tocItem) { tocItem.classList.add('text-slate-400', 'opacity-60'); tocItem.title = `第 ${index + 1} 题（已导入）`; }

                    saveBtn.className = 'card-save-btn px-4 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 font-bold text-[10px] border border-emerald-300 hover:bg-emerald-100 transition-colors';
                    saveBtn.innerHTML = '<i class="fa-solid fa-rotate-right"></i> <span>再次导入</span>';
                    saveBtn.disabled = false;
                    
                    const cb = card.querySelector('.card-select-checkbox');
                    if (cb) {
                        cb.disabled = true;
                        cb.checked = false;
                        cb.classList.add('opacity-50');
                    }
                    if (typeof updateSelectedCount === 'function') {
                        updateSelectedCount();
                    }

                    showToast(`第 ${index + 1} 题导入成功！`);
                    
                    loadCategories();
                    loadQuestions();
                    return true;
                } else {
                    throw new Error(data.message || '保存失败');
                }
            })
            .catch(err => {
                if (isParsedQuestionSaveContextCurrent(saveGeneration, index, q)) {
                    showToast(`第 ${index + 1} 题保存出错: ${err.message}`, 'error');
                    saveBtn.disabled = false;
                    saveBtn.innerHTML = '<i class="fa-solid fa-file-arrow-up"></i> <span>导入此题</span>';
                }
                throw err;
            });

            const trackedSave = saveOperation.finally(() => {
                if (parsedQuestionSaveInFlight.get(q) === trackedSave) {
                    parsedQuestionSaveInFlight.delete(q);
                }
            });
            parsedQuestionSaveInFlight.set(q, trackedSave);
            return trackedSave;
        }

        function confirmClearAllParsed() {
            if (blockImportResetWhileSaving()) {
                return;
            }
            if (confirm('确定要清空输入的试卷源码及拆解出的所有草稿题目吗？\n清空后，当前列表中的草稿题目及文件映射将恢复初始状态。')) {
                if (typeof performOrphanedTempCropsCleanup === 'function') {
                    performOrphanedTempCropsCleanup();
                }
                clearAllImportInputs();
                resetImportState(true);
                showToast('已成功清空所有录入数据与拆解草稿！', 'info');
            }
        }

        function clearAllParsedSources() {
            const inputs = document.querySelectorAll('#parsedCardsContainer .card-source');
            if (inputs.length === 0) {
                showToast('当前拆解列表为空！', 'warning');
                return;
            }
            inputs.forEach(input => {
                input.value = '';
            });
            showToast('已成功一键清空所有拆解题目的试卷标题来源！', 'success');
        }

        function getCheckedUnsavedIndices() {
            const indices = [];
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                const idx = parseInt(cb.getAttribute('data-index'), 10);
                const q = parsedQuestionsData[idx];
                if (q && !q.saved && cb.checked && isCardVisibleByFilter(q)) {
                    indices.push(idx);
                }
            });
            return indices;
        }

        function updateSelectedCount() {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            let unsavedCount = 0;
            let checkedCount = 0;
            
            checkboxes.forEach(cb => {
                const idx = parseInt(cb.getAttribute('data-index'), 10);
                const q = parsedQuestionsData[idx];
                if (q && !q.saved && isCardVisibleByFilter(q)) {
                    unsavedCount++;
                    if (cb.checked) {
                        checkedCount++;
                    }
                }
            });
            
            const badge = document.getElementById('selectedCountBadge');
            if (badge) {
                badge.textContent = `已选 ${checkedCount} / ${unsavedCount} 题`;
            }
            
            const selectAllCb = document.getElementById('selectAllCheckbox');
            if (selectAllCb) {
                if (unsavedCount === 0) {
                    selectAllCb.checked = false;
                    selectAllCb.indeterminate = false;
                    selectAllCb.disabled = true;
                } else {
                    selectAllCb.disabled = false;
                    if (checkedCount === unsavedCount) {
                        selectAllCb.checked = true;
                        selectAllCb.indeterminate = false;
                    } else if (checkedCount === 0) {
                        selectAllCb.checked = false;
                        selectAllCb.indeterminate = false;
                    } else {
                        selectAllCb.checked = false;
                        selectAllCb.indeterminate = true;
                    }
                }
            }
            
            const btnText = document.getElementById('saveAllParsedBtnText');
            if (btnText) {
                btnText.textContent = checkedCount > 0 ? `导入选中 (${checkedCount})` : `导入选中题目`;
            }

            const saveAllBtn = document.getElementById('saveAllParsedBtn');
            if (saveAllBtn) {
                if (checkedCount === 0) {
                    saveAllBtn.disabled = true;
                    saveAllBtn.className = "flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-slate-100 text-slate-400 font-bold text-xs border border-slate-200 shadow-sm cursor-not-allowed transition-all";
                } else {
                    saveAllBtn.disabled = false;
                    saveAllBtn.className = "flex items-center space-x-1.5 px-4 py-2 rounded-xl bg-brand-600/80 hover:bg-brand-600 text-white font-bold text-xs backdrop-blur-sm border border-brand-500/20 shadow-sm transition-all active:scale-95 cursor-pointer";
                }
            }
        }

        function toggleSelectAllParsed(checked) {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                if (cb.disabled) return;
                const idx = parseInt(cb.getAttribute('data-index'), 10);
                const q = parsedQuestionsData[idx];
                if (q && !isCardVisibleByFilter(q)) return;
                cb.checked = checked;
            });
            updateSelectedCount();
        }

        function invertSelectParsed() {
            const checkboxes = document.querySelectorAll('.card-select-checkbox');
            checkboxes.forEach(cb => {
                if (cb.disabled) return;
                const idx = parseInt(cb.getAttribute('data-index'), 10);
                const q = parsedQuestionsData[idx];
                if (q && !isCardVisibleByFilter(q)) return;
                cb.checked = !cb.checked;
            });
            updateSelectedCount();
        }

        // ==========================================
        // 拆解结果审查筛选（未导入 / 已导入 / 全部）
        // ==========================================
        // 按当前筛选判断单题是否可见：仅控制 DOM 显示，不改动数组下标
        function isCardVisibleByFilter(q) {
            const f = window.__parsedReviewFilter;
            if (f === 'imported') return !!q.saved;
            if (f === 'unimported') return !q.saved;
            return true; // all
        }

        // 刷新筛选 Tab 上的计数徽标
        function refreshFilterCounts() {
            const total = parsedQuestionsData.length;
            const imported = parsedQuestionsData.filter(q => !!q.saved).length;
            const unimported = total - imported;
            const set = (id, n) => { const el = document.getElementById(id); if (el) el.textContent = n; };
            set('filterCountAll', total);
            set('filterCountImported', imported);
            set('filterCountUnimported', unimported);
        }

        // 根据当前筛选：切换卡片可见性、刷新分组头可见题数、空状态提示、计数
        function applyReviewFilter() {
            const container = document.getElementById('parsedCardsContainer');
            if (!container) return;
            let visibleCount = 0;
            parsedQuestionsData.forEach((q, index) => {
                const card = document.getElementById(`parsed-card-${index}`);
                if (!card) return;
                const visible = isCardVisibleByFilter(q);
                card.classList.toggle('hidden', !visible);
                if (visible) visibleCount++;
            });
            // 分组头：按可见题数刷新计数，整组为空则隐藏
            if (parsedFileGroups && parsedFileGroups.length) {
                parsedFileGroups.forEach((group, gi) => {
                    const header = container.querySelector(`[data-group-header="${gi}"]`);
                    if (!header) return;
                    const nextStart = (gi + 1 < parsedFileGroups.length) ? parsedFileGroups[gi + 1].startIndex : parsedQuestionsData.length;
                    let grpVisible = 0;
                    for (let i = group.startIndex; i < nextStart; i++) {
                        const q = parsedQuestionsData[i];
                        if (q && isCardVisibleByFilter(q)) grpVisible++;
                    }
                    const countSpan = header.querySelector('[data-group-visible]');
                    if (countSpan) countSpan.textContent = `${grpVisible} 题`;
                    header.classList.toggle('hidden', grpVisible === 0);
                });
            }
            const badge = document.getElementById('parsedCountBadge');
            if (badge) badge.textContent = `共 ${visibleCount} 题`;
            // 空状态提示（有题但当前筛选下全被隐藏）
            const oldEmpty = document.getElementById('reviewFilterEmpty');
            if (oldEmpty) oldEmpty.remove();
            if (visibleCount === 0 && parsedQuestionsData.length > 0) {
                const div = document.createElement('div');
                div.id = 'reviewFilterEmpty';
                const f = window.__parsedReviewFilter;
                div.className = 'p-12 text-center text-slate-400 text-xs';
                div.textContent = f === 'imported' ? '暂无已导入的题目。' : (f === 'unimported' ? '🎉 所有题目都已成功导入题库！' : '当前筛选下没有题目。');
                container.appendChild(div);
            }
            refreshFilterCounts();
            updateSelectedCount();
        }

        // 切换筛选：更新 Tab 高亮 + 应用筛选（供 HTML onclick 调用）
        function setReviewFilter(f) {
            window.__parsedReviewFilter = f;
            document.querySelectorAll('.review-filter-btn').forEach(btn => {
                const active = btn.getAttribute('data-filter') === f;
                btn.classList.toggle('bg-brand-600', active);
                btn.classList.toggle('text-white', active);
                btn.classList.toggle('text-slate-500', !active);
                btn.classList.toggle('hover:bg-slate-100', !active);
            });
            applyReviewFilter();
        }

        function saveAllParsedQuestions() {
            const selectedIndices = getCheckedUnsavedIndices();
            const batchGeneration = parsedQuestionsGeneration;

            if (selectedIndices.length === 0) {
                const unsavedCount = parsedQuestionsData.filter(q => !q.saved).length;
                if (unsavedCount === 0) {
                    showToast('所有题目已成功导入！', 'info');
                    return;
                }
                // 方案B：队列还有文件未拆完时，若用户未手动勾选，自动勾选已出现的题
                // （即已拆解完成文件的题，仍在拆/待拆文件的题尚未进入列表），先导入已完成部分。
                if (window.__importQueueHasPending) {
                    toggleSelectAllParsed(true);
                    const autoIndices = getCheckedUnsavedIndices();
                    if (autoIndices.length > 0) {
                        showToast(`队列尚有文件未拆完，先导入已拆解完成的 ${autoIndices.length} 道题目。`, 'info');
                        updateSelectedCount();
                        selectedIndices.length = 0;
                        autoIndices.forEach(i => selectedIndices.push(i));
                    } else {
                        showToast('当前已拆解的文件题目均已导入，剩余文件拆解完成后再导入。', 'info');
                        return;
                    }
                } else {
                    showToast('请先勾选需要导入的题目！', 'warning');
                    return;
                }
            }

            const mainBtn = document.getElementById('saveAllParsedBtn');
            if (!mainBtn) return;
            
            mainBtn.disabled = true;

            const btnText = document.getElementById('saveAllParsedBtnText');
            const originalText = btnText ? btnText.textContent : '导入选中题目';
            if (btnText) {
                btnText.textContent = '批量入库中...';
            }

            const icon = mainBtn.querySelector('i');
            const originalIconClass = icon ? icon.className : 'fa-solid fa-cloud-arrow-up';
            if (icon) {
                icon.className = 'fa-solid fa-spinner animate-spin';
            }

            // 方案A：顶部徽标实时显示导入进度「导入中：完成/总数」
            const progressBadge = document.getElementById('selectedCountBadge');
            const totalToImport = selectedIndices.length;
            let doneCount = 0;
            const setImportProgress = () => {
                if (progressBadge) {
                    progressBadge.textContent = `导入中：已导入 ${doneCount} / ${totalToImport} 题`;
                }
            };
            setImportProgress();

            showToast(`正在批量导入 ${selectedIndices.length} 道勾选题目，请稍候...`);

            const promises = selectedIndices.map(idx =>
                saveParsedQuestion(idx)
                    .then(
                        res => {
                            // 每完成一题（成功/重复/后端失败）都推进计数
                            doneCount++;
                            setImportProgress();
                            return res;
                        },
                        err => {
                            // 前端校验拒绝（题干空/缺章节/卡片缺失）也会走到这里，
                            // 同样计入已完成，避免徽标卡在「N-1/N」永不收尾
                            doneCount++;
                            setImportProgress();
                            return null;
                        }
                    )
            );

            Promise.all(promises)
                .then(results => {
                    if (batchGeneration !== parsedQuestionsGeneration) {
                        return;
                    }
                    const successCount = results.filter(r => r === true).length;

                    updateSelectedCount();
                    applyReviewFilter();
                    const remainingUnsavedCount = parsedQuestionsData.filter(q => !q.saved).length;

                    if (remainingUnsavedCount === 0) {
                        showToast(`批量导入完成！共 ${successCount} 道题目已全部成功导入本地库！`, 'success');

                        setTimeout(() => {
                            if (batchGeneration !== parsedQuestionsGeneration) {
                                return;
                            }
                            if (blockImportResetWhileSaving()) {
                                return;
                            }
                            // 方案B：若批量队列里还有未拆解（pending/parsing）的文件，
                            // 不关闭弹窗、不清空队列，仅提示用户继续拆解剩余文件；
                            // 只有队列也全部处理完（无 pending/parsing）时才整体收尾。
                            if (window.__importQueueHasPending) {
                                showToast(`本批 ${successCount} 道题已导入！队列还有文件待拆解，请继续点“开始拆解剩余文件”。`, 'success');
                                return;
                            }
                            clearAllImportInputs();
                            resetImportState(false);
                            closeImportModal();
                        }, 1500);
                    } else {
                        showToast(`批量导入已完成！成功: ${successCount}/${selectedIndices.length}。剩余未导入的题目已保留，请确认。`, 'warning');
                    }
                })
                .catch(err => {
                    if (batchGeneration !== parsedQuestionsGeneration) {
                        return;
                    }
                    showToast(`批量导入时发生严重错误: ${err.message}`, 'error');
                })
                .finally(() => {
                    if (batchGeneration !== parsedQuestionsGeneration) {
                        return;
                    }
                    // 恢复按钮外观，但阶段一（队列仍有未拆文件）需保持禁用，避免误导入
                    if (icon) {
                        icon.className = originalIconClass;
                    }
                    if (btnText) {
                        btnText.textContent = originalText;
                    }
                    if (!window.__importQueueHasPending) {
                        mainBtn.disabled = false;
                    }
                    // 导入结束：徽标恢复为「已选 X / Y 题」
                    updateSelectedCount();
                });
        }


        // ==========================================
        // SIDEBAR QUESTION SOURCE AUTOCOMPLETE FILTER
        // ==========================================
        function setupSourceFilterAutocomplete() {
            const sourceInput = document.getElementById('filterSource');
            const suggestionsDiv = document.getElementById('filterSourceSuggestions');
            const toggleBtn = document.getElementById('toggleFilterSourceBtn');
            const clearBtn = document.getElementById('clearFilterSourceBtn');
            const chevronIcon = document.getElementById('chevronFilterSourceIcon');
            
            if (!sourceInput || !suggestionsDiv) return;

            function fetchSources(callback) {
                fetch('/api/sources')
                    .then(r => r.json())
                    .then(sources => {
                        allSourcesList = sources;
                        if (callback) callback(sources);
                    })
                    .catch(err => {
                        console.error('Failed to fetch sources:', err);
                    });
            }
            
            function renderSuggestions(list) {
                suggestionsDiv.innerHTML = '';
                if (list.length === 0) {
                    suggestionsDiv.innerHTML = '<div class="px-3 py-2 text-[10px] text-slate-400 italic text-center select-none">无匹配来源</div>';
                    suggestionsDiv.classList.remove('hidden');
                    chevronIcon.classList.add('rotate-180');
                    return;
                }

                list.forEach(src => {
                    const item = document.createElement('div');
                    item.className = "px-3 py-2 hover:bg-slate-50 text-xs text-slate-700 cursor-pointer select-none truncate font-medium transition-colors border-b border-slate-100/50 last:border-b-0";
                    item.textContent = src;
                    item.addEventListener('click', () => {
                        sourceInput.value = src;
                        suggestionsDiv.classList.add('hidden');
                        chevronIcon.classList.remove('rotate-180');
                        updateClearButtonVisibility();
                        currentBankPage = 1;
                        currentDraftPage = 1;
                        if (activeSidebarTab === 'bank') {
                            loadQuestions();
                        } else {
                            loadDrafts();
                        }
                    });
                    suggestionsDiv.appendChild(item);
                });
                suggestionsDiv.classList.remove('hidden');
                chevronIcon.classList.add('rotate-180');
            }
            
            function updateClearButtonVisibility() {
                if (sourceInput.value.trim() !== '') {
                    clearBtn.classList.remove('hidden');
                } else {
                    clearBtn.classList.add('hidden');
                }
            }
            
            sourceInput.addEventListener('focus', () => {
                fetchSources(sources => {
                    const val = sourceInput.value.trim().toLowerCase();
                    if (val === '') {
                        renderSuggestions(sources);
                    } else {
                        const filtered = sources.filter(s => s.toLowerCase().includes(val));
                        renderSuggestions(filtered);
                    }
                });
            });
            
            sourceInput.addEventListener('input', () => {
                updateClearButtonVisibility();
                const val = sourceInput.value.trim().toLowerCase();
                if (val === '') {
                    renderSuggestions(allSourcesList);
                } else {
                    const filtered = allSourcesList.filter(s => s.toLowerCase().includes(val));
                    renderSuggestions(filtered);
                }
            });
            
            sourceInput.addEventListener('change', () => {
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });
            
            sourceInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                    currentBankPage = 1;
                    currentDraftPage = 1;
                    if (activeSidebarTab === 'bank') {
                        loadQuestions();
                    } else {
                        loadDrafts();
                    }
                }
            });
            
            toggleBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                if (!suggestionsDiv.classList.contains('hidden')) {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                } else {
                    sourceInput.focus();
                }
            });
            
            clearBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                sourceInput.value = '';
                updateClearButtonVisibility();
                suggestionsDiv.classList.add('hidden');
                chevronIcon.classList.remove('rotate-180');
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });
            
            document.addEventListener('click', (e) => {
                if (!e.target.closest('#filterSourceContainer')) {
                    suggestionsDiv.classList.add('hidden');
                    chevronIcon.classList.remove('rotate-180');
                }
            });
        }

        // ==========================================
        // LaTeX TITLE AUTO-EXTRACTION HELPERS
        // ==========================================
        function extractLatexBraceGroup(latex, start) {
            if (!latex || start < 0 || latex[start] !== '{') return null;
            let depth = 0;
            for (let i = start; i < latex.length; i++) {
                let slashCount = 0;
                for (let j = i - 1; j >= 0 && latex[j] === '\\'; j--) slashCount++;
                const escaped = slashCount % 2 === 1;
                if (latex[i] === '{' && !escaped) depth++;
                if (latex[i] === '}' && !escaped) {
                    depth--;
                    if (depth === 0) return {content: latex.slice(start + 1, i), end: i + 1};
                }
            }
            return null;
        }

        function findLatexCommandGroup(latex, commands) {
            for (const command of commands) {
                const pattern = new RegExp('\\\\' + command + '\\b', 'g');
                let match;
                while ((match = pattern.exec(latex)) !== null) {
                    let cursor = match.index + match[0].length;
                    while (cursor < latex.length && /\s/.test(latex[cursor])) cursor++;
                    const group = extractLatexBraceGroup(latex, cursor);
                    if (group) return {command, content: group.content};
                }
            }
            return null;
        }

        function extractTitleFromLatex(latex) {
            if (!latex) return "";
            const commandTitle = findLatexCommandGroup(latex, ['title', 'chead', 'lhead', 'rhead']);
            if (commandTitle) {
                const clean = cleanLatexFormatting(commandTitle.content);
                if (clean && !clean.includes('页') && !clean.includes('绝密')) return clean;
            }

            const topPart = latex.slice(0, 1500);
            const match = topPart.match(/\\begin\s*\{center\}([\s\S]*?)\\end\s*\{center\}/);
            if (match && match[1]) {
                let content = match[1].trim();
                content = content.replace(/\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt)/g, '');
                content = content.replace(/\\textbf\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/\\heiti\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/\\kt\s*\{([^}]+)\}/g, '$1');
                content = content.replace(/[\{\}]/g, '');
                
                const lines = content.split('\n').map(l => l.trim()).filter(l => l && !l.startsWith('%') && !l.includes('\\includegraphics') && !l.includes('\\chead') && !l.includes('\\lhead'));
                if (lines.length > 0) {
                    for (let line of lines) {
                        line = cleanLatexFormatting(line);
                        if (line.includes("中学") || line.includes("试卷") || line.includes("试题") || line.includes("考试") || line.includes("期") || line.includes("测试") || line.includes("年")) {
                            return line;
                        }
                    }
                    return cleanLatexFormatting(lines[0]);
                }
            }
            
            return "";
        }

        function cleanLatexFormatting(str) {
            if (!str) return "";
            let cleaned = str;
            for (let i = 0; i < 5; i++) {
                const next = cleaned
                    .replace(/\\text(?:bf|it|sf|tt)\s*\{([^{}]*)\}/g, '$1')
                    .replace(/\\(?:heiti|kt|kaishu|songti|fangsong)\s*\{([^{}]*)\}/g, '$1');
                if (next === cleaned) break;
                cleaned = next;
            }
            return cleaned
                .replace(/\\(large|Large|LARGE|huge|Huge|small|bf|bfseries|it|itshape|sf|tt)/g, '')
                .replace(/\\sffamily/g, '')
                .replace(/\\centering/g, '')
                .replace(/[\{\}]/g, '')
                .replace(/\\\\/g, '')
                .trim();
        }

        // App Initialization
        document.addEventListener('DOMContentLoaded', () => {
            // Check configs
            fetchConfigStatus();
            
            // Load and update drafts count
            updateDraftCountBadge();

            // Bind search input to loadQuestions / loadDrafts dynamically
            document.getElementById('searchInput').addEventListener('input', () => {
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });

            // Bind filterType and filterDifficulty select elements dynamically to loadQuestions / loadDrafts
            document.getElementById('filterType').addEventListener('change', () => {
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });

            document.getElementById('filterDifficulty').addEventListener('change', () => {
                currentBankPage = 1;
                currentDraftPage = 1;
                if (activeSidebarTab === 'bank') {
                    loadQuestions();
                } else {
                    loadDrafts();
                }
            });

            // Bind filterSort change event
            const filterSortEl = document.getElementById('filterSort');
            if (filterSortEl) {
                filterSortEl.addEventListener('change', () => {
                    currentBankPage = 1;
                    currentDraftPage = 1;
                    if (activeSidebarTab === 'bank') {
                        loadQuestions();
                    } else {
                        loadDrafts();
                    }
                });
            }

            // Load Cascade Category Tree
            loadCategories();
            
            // Load saved questions list
            loadQuestions();
            
            // Load and populate related questions dropdown
            refreshRelatedDropdown();

            // Set up related question display number input two-way synchronization
            const relatedNumInput = document.getElementById('editRelatedQuestionNum');
            const relatedSelect = document.getElementById('editRelatedQuestion');
            if (relatedNumInput && relatedSelect) {
                relatedNumInput.addEventListener('input', () => {
                    const val = relatedNumInput.value.trim();
                    if (!val) {
                        relatedSelect.value = '';
                    } else {
                        let found = false;
                        for (let i = 0; i < relatedSelect.options.length; i++) {
                            const opt = relatedSelect.options[i];
                            if (opt.getAttribute('data-seq-num') === val) {
                                relatedSelect.value = opt.value;
                                found = true;
                                break;
                            }
                        }
                        if (!found) {
                            relatedSelect.value = '';
                        }
                    }
                });

                relatedSelect.addEventListener('change', () => {
                    const selectedOpt = relatedSelect.options[relatedSelect.selectedIndex];
                    if (selectedOpt && selectedOpt.value) {
                        relatedNumInput.value = selectedOpt.getAttribute('data-seq-num') || '';
                    } else {
                        relatedNumInput.value = '';
                    }
                });
            }

            // Set up debounced event listeners for realtime markdown preview
            setupRealtimePreviews();

            // Setup drag-and-drop & clipboard listeners for illustrations & OCR
            setupUploadHandlers();
            
            // Setup resizers
            initResizers();

            // Setup searchable source filter autocomplete
            setupSourceFilterAutocomplete();

            // Setup LaTeX batch import handlers
            setupImportFileHandlers();

            // Initialize empty original state
            backupEditorState(null, null);

            // ================== TikZ Geometry Drawing & AI Correction Helpers (双通道分离设计) ==================
            function beginEditorBoundRequest(button, idleHtml) {
                const editorSession = EditorState.snapshot();
                const requestToken = {};
                button._mathbankEditorRequestToken = requestToken;
                return () => {
                    if (button._mathbankEditorRequestToken !== requestToken) return false;
                    button.disabled = false;
                    button.innerHTML = idleHtml;
                    return EditorState.isCurrent(editorSession);
                };
            }

            window.extractTikzCodeFromTextarea = function(textareaId) {
                const textarea = document.getElementById(textareaId);
                if (!textarea) return;
                
                let text = textarea.value;
                const tikzRegex = /(\\begin\s*\{\s*tikzpicture\s*\}[\s\S]*?\\end\s*\{\s*tikzpicture\s*\})/i;
                const match = text.match(tikzRegex);
                
                if (match) {
                    const tikzBlock = match[1].trim();
                    const isContent = (textareaId === 'editContent');
                    const targetInputId = isContent ? 'editContentTikzCode' : 'editAnswerTikzCode';
                    const targetContainerId = isContent ? 'contentTikzContainer' : 'answerTikzContainer';
                    
                    // Show Container
                    const container = document.getElementById(targetContainerId);
                    if (container) container.classList.remove('hidden');
                    
                    // Fill input
                    const tikzInput = document.getElementById(targetInputId);
                    if (tikzInput) {
                        tikzInput.value = tikzBlock;
                        tikzInput.dispatchEvent(new Event('input'));
                    }
                    
                    // Clear from textarea
                    text = text.replace(tikzRegex, '').trim();
                    textarea.value = text;
                    textarea.dispatchEvent(new Event('input'));
                    
                    // Auto-compile
                    const compileFn = isContent ? window.renderContentTikzToImage : window.renderAnswerTikzToImage;
                    if (typeof compileFn === 'function') {
                        const editorSession = EditorState.snapshot();
                        const targetName = isContent ? '题干' : '解答';
                        showToast(`🎉 检测到${targetName}中的 TikZ 代码！已自动提取并开始编译。`, 'info');
                        setTimeout(() => {
                            if (!EditorState.isCurrent(editorSession)) return;
                            compileFn();
                            
                            // Scroll to focus
                            if (tikzInput) {
                                tikzInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                tikzInput.focus();
                            }
                        }, 200);
                    }
                }
            };

            // 题干清理与编译
            window.clearContentTikzCode = function() {
                if (confirm("确定要清空题干 TikZ 代码吗？")) {
                    document.getElementById('editContentTikzCode').value = '';
                    document.getElementById('contentTikzPreviewImage').classList.add('hidden');
                    document.getElementById('contentTikzPreviewImage').src = '';
                    document.getElementById('contentTikzPreviewPlaceholder').classList.remove('hidden');
                    document.getElementById('contentTikzStatusText').textContent = '已清空';
                }
            };

            window.renderContentTikzToImage = function() {
                const tikzCode = document.getElementById('editContentTikzCode').value;
                if (!tikzCode.trim()) {
                    showToast('请输入题干 TikZ 绘图代码后重试。', 'error');
                    return;
                }
                
                const btn = document.getElementById('btnRenderContentTikz');
                const statusText = document.getElementById('contentTikzStatusText');
                const placeholder = document.getElementById('contentTikzPreviewPlaceholder');
                const previewImg = document.getElementById('contentTikzPreviewImage');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>编译中...</span>';
                statusText.textContent = '编译中...';
                const finishRequest = beginEditorBoundRequest(
                    btn,
                    '<i class="fa-solid fa-circle-play"></i> <span>编译并插入题干</span>'
                );
                
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                
                fetch('/api/render_tikz', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '编译失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    if (!finishRequest()) return;
                    
                    if (data.status === 'success') {
                        showToast('题干 TikZ 几何图编译成功，已插入插图列表！');
                        statusText.textContent = '编译成功';
                        
                        placeholder.classList.add('hidden');
                        previewImg.src = data.image_path + '?t=' + new Date().getTime();
                        previewImg.classList.remove('hidden');
                        
                        const cleanPath = data.image_path;
                        const contentInput = document.getElementById('editContent');
                        const oldPath = window.contentLastCompiledTikzPath;
                        
                        // Replace previous compiled path if exists
                        if (oldPath && oldPath !== cleanPath) {
                            const idx = uploadedImages.indexOf(oldPath);
                            if (idx > -1) {
                                uploadedImages.splice(idx, 1);
                            }
                            if (contentInput && contentInput.value.includes(oldPath)) {
                                contentInput.value = contentInput.value.split(oldPath).join(cleanPath);
                                contentInput.dispatchEvent(new Event('input'));
                            }
                        }
                        
                        if (!uploadedImages.includes(cleanPath)) {
                            uploadedImages.push(cleanPath);
                        }
                        renderIllustrationBadges();
                        
                        if (contentInput && !contentInput.value.includes(cleanPath)) {
                            contentInput.value += `\n\n![](${cleanPath})`;
                            contentInput.dispatchEvent(new Event('input'));
                        }
                        
                        window.contentLastCompiledTikzPath = cleanPath;
                    }
                })
                .catch(err => {
                    if (!finishRequest()) return;
                    statusText.textContent = '编译出错';
                    placeholder.classList.remove('hidden');
                    previewImg.classList.add('hidden');
                    showToast('题干 TikZ 编译出错: ' + err.message, 'error');
                });
            };

            window.correctContentTikzWithAI = function() {
                let originalPath = window.lastOcrOriginalImagePath || '';
                if (!originalPath) {
                    const originalImgs = uploadedImages.filter(path => !path.includes('/tikz_'));
                    if (originalImgs.length > 0) {
                        originalPath = originalImgs[0];
                    }
                }
                
                if (!originalPath) {
                    showToast('无法纠错：当前题目未检测到任何原始截图作为参考比对模板。', 'error');
                    return;
                }
                
                const tikzCode = document.getElementById('editContentTikzCode').value;
                const btn = document.getElementById('btnCorrectContentTikz');
                const statusText = document.getElementById('contentTikzStatusText');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>纠错中...</span>';
                statusText.textContent = '纠错中...';
                const finishRequest = beginEditorBoundRequest(
                    btn,
                    '<i class="fa-solid fa-wand-magic-sparkles animate-pulse"></i> <span>AI 纠错</span>'
                );
                
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                formData.append('original_image_path', originalPath);
                
                const userPromptInput = document.getElementById('contentTikzUserPrompt');
                const userPrompt = userPromptInput ? userPromptInput.value.trim() : '';
                formData.append('user_prompt', userPrompt);
                
                fetch('/api/correct_tikz', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '纠错失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    if (!finishRequest()) return;
                    
                    if (data.status === 'success') {
                        showToast('AI 纠错完成，已回填并重新编译代码！');
                        document.getElementById('editContentTikzCode').value = data.corrected_code;
                        document.getElementById('editContentTikzCode').dispatchEvent(new Event('input'));
                        window.renderContentTikzToImage();
                    }
                })
                .catch(err => {
                    if (!finishRequest()) return;
                    statusText.textContent = '纠错失败';
                    showToast('AI 纠错出错: ' + err.message, 'error');
                });
            };

            // 解答清理与编译
            window.clearAnswerTikzCode = function() {
                if (confirm("确定要清空解答 TikZ 代码吗？")) {
                    document.getElementById('editAnswerTikzCode').value = '';
                    document.getElementById('answerTikzPreviewImage').classList.add('hidden');
                    document.getElementById('answerTikzPreviewImage').src = '';
                    document.getElementById('answerTikzPreviewPlaceholder').classList.remove('hidden');
                    document.getElementById('answerTikzStatusText').textContent = '已清空';
                }
            };

            window.renderAnswerTikzToImage = function() {
                const tikzCode = document.getElementById('editAnswerTikzCode').value;
                if (!tikzCode.trim()) {
                    showToast('请输入解答 TikZ 绘图代码后重试。', 'error');
                    return;
                }
                
                const btn = document.getElementById('btnRenderAnswerTikz');
                const statusText = document.getElementById('answerTikzStatusText');
                const placeholder = document.getElementById('answerTikzPreviewPlaceholder');
                const previewImg = document.getElementById('answerTikzPreviewImage');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>编译中...</span>';
                statusText.textContent = '编译中...';
                const finishRequest = beginEditorBoundRequest(
                    btn,
                    '<i class="fa-solid fa-play"></i> <span>编译并插入解答</span>'
                );
                
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                
                fetch('/api/render_tikz', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '编译失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    if (!finishRequest()) return;
                    
                    if (data.status === 'success') {
                        showToast('解答 TikZ 几何图编译成功，已插入解答文本中！');
                        statusText.textContent = '编译成功';
                        
                        placeholder.classList.add('hidden');
                        previewImg.src = data.image_path + '?t=' + new Date().getTime();
                        previewImg.classList.remove('hidden');
                        
                        const cleanPath = data.image_path;
                        const answerInput = document.getElementById('editAnswerMarkdown');
                        const oldPath = window.answerLastCompiledTikzPath;
                        
                        // Replace previous compiled path if exists
                        if (oldPath && oldPath !== cleanPath) {
                            if (answerInput && answerInput.value.includes(oldPath)) {
                                answerInput.value = answerInput.value.split(oldPath).join(cleanPath);
                                answerInput.dispatchEvent(new Event('input'));
                            }
                        }
                        
                        if (answerInput && !answerInput.value.includes(cleanPath)) {
                            answerInput.value += `\n\n![](${cleanPath})`;
                            answerInput.dispatchEvent(new Event('input'));
                        }
                        
                        window.answerLastCompiledTikzPath = cleanPath;
                    }
                })
                .catch(err => {
                    if (!finishRequest()) return;
                    statusText.textContent = '编译出错';
                    placeholder.classList.remove('hidden');
                    previewImg.classList.add('hidden');
                    showToast('解答 TikZ 编译出错: ' + err.message, 'error');
                });
            };

            window.correctAnswerTikzWithAI = function() {
                let originalPath = window.lastOcrOriginalImagePath || '';
                if (!originalPath) {
                    const originalImgs = uploadedImages.filter(path => !path.includes('/tikz_'));
                    if (originalImgs.length > 0) {
                        originalPath = originalImgs[0];
                    }
                }
                
                if (!originalPath) {
                    showToast('无法纠错：当前题目未检测到任何原始截图作为参考比对模板。', 'error');
                    return;
                }
                
                const tikzCode = document.getElementById('editAnswerTikzCode').value;
                const btn = document.getElementById('btnCorrectAnswerTikz');
                const statusText = document.getElementById('answerTikzStatusText');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>纠错中...</span>';
                statusText.textContent = '纠错中...';
                const finishRequest = beginEditorBoundRequest(
                    btn,
                    '<i class="fa-solid fa-wand-magic-sparkles animate-pulse"></i> <span>AI 纠错</span>'
                );
                
                const formData = new FormData();
                formData.append('tikz_code', tikzCode);
                formData.append('original_image_path', originalPath);
                
                const userPromptInput = document.getElementById('answerTikzUserPrompt');
                const userPrompt = userPromptInput ? userPromptInput.value.trim() : '';
                formData.append('user_prompt', userPrompt);
                
                fetch('/api/correct_tikz', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '纠错失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    if (!finishRequest()) return;
                    
                    if (data.status === 'success') {
                        showToast('AI 纠错完成，已回填并重新编译代码！');
                        document.getElementById('editAnswerTikzCode').value = data.corrected_code;
                        document.getElementById('editAnswerTikzCode').dispatchEvent(new Event('input'));
                        window.renderAnswerTikzToImage();
                    }
                })
                .catch(err => {
                    if (!finishRequest()) return;
                    statusText.textContent = '纠错失败';
                    showToast('AI 纠错出错: ' + err.message, 'error');
                });
            };

            window.drawContentTikzFromImageWithAI = function() {
                let originalPath = window.lastOcrOriginalImagePath || '';
                if (!originalPath) {
                    const originalImgs = typeof uploadedImages !== 'undefined' ? uploadedImages.filter(path => !path.includes('/tikz_')) : [];
                    if (originalImgs.length > 0) {
                        originalPath = originalImgs[0];
                    }
                }
                
                if (!originalPath) {
                    showToast('当前题目未检测到任何插图可供 AI 识别绘图。', 'error');
                    return;
                }
                
                const latexContent = document.getElementById('editContent').value;
                const btn = document.getElementById('btnDrawContentTikzFromImage');
                const statusText = document.getElementById('contentTikzStatusText');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>识别绘制中...</span>';
                statusText.textContent = '识别绘制中...';
                const finishRequest = beginEditorBoundRequest(
                    btn,
                    '<i class="fa-solid fa-circle-nodes"></i> <span>AI 识图绘图</span>'
                );
                
                const formData = new FormData();
                formData.append('image_path', originalPath);
                if (latexContent && latexContent.trim()) {
                    formData.append('latex_content', latexContent);
                }
                
                fetch('/api/ai/draw_tikz_from_image', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '识别绘图失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    if (!finishRequest()) return;
                    
                    if (data.status === 'success') {
                        showToast('AI 识图绘图完成，已生成 TikZ 代码并开始自动编译！');
                        document.getElementById('editContentTikzCode').value = data.tikz_code;
                        document.getElementById('editContentTikzCode').dispatchEvent(new Event('input'));
                        window.renderContentTikzToImage();
                    } else {
                        throw new Error(data.message || '识别绘图失败');
                    }
                })
                .catch(err => {
                    if (!finishRequest()) return;
                    statusText.textContent = '识别绘图失败';
                    showToast('AI 识图绘图出错: ' + err.message, 'error');
                });
            };

            window.drawAnswerTikzFromImageWithAI = function() {
                let originalPath = window.lastOcrOriginalImagePath || '';
                if (!originalPath) {
                    const originalImgs = typeof uploadedImages !== 'undefined' ? uploadedImages.filter(path => !path.includes('/tikz_')) : [];
                    if (originalImgs.length > 0) {
                        originalPath = originalImgs[0];
                    }
                }
                
                if (!originalPath) {
                    showToast('当前题目未检测到任何插图可供 AI 识别绘图。', 'error');
                    return;
                }
                
                const latexContent = document.getElementById('editContent').value;
                const btn = document.getElementById('btnDrawAnswerTikzFromImage');
                const statusText = document.getElementById('answerTikzStatusText');
                
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i> <span>识别绘制中...</span>';
                statusText.textContent = '识别绘制中...';
                const finishRequest = beginEditorBoundRequest(
                    btn,
                    '<i class="fa-solid fa-circle-nodes"></i> <span>AI 识图绘图</span>'
                );
                
                const formData = new FormData();
                formData.append('image_path', originalPath);
                if (latexContent && latexContent.trim()) {
                    formData.append('latex_content', latexContent);
                }
                
                fetch('/api/ai/draw_tikz_from_image', {
                    method: 'POST',
                    body: formData
                })
                .then(r => {
                    if (!r.ok) {
                        return r.json().then(data => { throw new Error(data.detail || '识别绘图失败') });
                    }
                    return r.json();
                })
                .then(data => {
                    if (!finishRequest()) return;
                    
                    if (data.status === 'success') {
                        showToast('AI 识图绘图完成，已生成 TikZ 代码并开始自动编译！');
                        document.getElementById('editAnswerTikzCode').value = data.tikz_code;
                        document.getElementById('editAnswerTikzCode').dispatchEvent(new Event('input'));
                        window.renderAnswerTikzToImage();
                    } else {
                        throw new Error(data.message || '识别绘图失败');
                    }
                })
                .catch(err => {
                    if (!finishRequest()) return;
                    statusText.textContent = '识别绘图失败';
                    showToast('AI 识图绘图出错: ' + err.message, 'error');
                });
            };
        });

        // 单题一键补全/重生成 AI 解答
        async function generateSingleAnswer(index) {
            const answerGeneration = parsedQuestionsGeneration;
            const q = parsedQuestionsData[index];
            if (!q) return;
            const card = document.getElementById(`parsed-card-${index}`);
            if (!card) return;
            const requestIsCurrent = () => isParsedQuestionSaveContextCurrent(
                answerGeneration,
                index,
                q
            );

            const btn = card.querySelector('.card-solve-btn');
            const answerTextarea = card.querySelector('.card-answer-textarea');
            const answerPrev = card.querySelector('.card-answer-preview');
            
            if (btn) {
                btn.disabled = true;
                btn.innerHTML = '<i class="fa-solid fa-spinner animate-spin"></i><span>AI 解答中...</span>';
            }
            if (answerPrev) {
                answerPrev.innerHTML = '<div class="flex items-center space-x-2 text-indigo-600 font-bold text-xs py-2"><i class="fa-solid fa-brain animate-bounce"></i><span>AI 正在深入推导解答步骤，请稍候...</span></div>';
            }

            try {
                const formData = new FormData();
                formData.append('content', q.content || '');
                formData.append('question_type', q.question_type || 'detailed_answer');
                formData.append('stream', 'false');

                const res = await fetch('/api/ai/solve', {
                    method: 'POST',
                    headers: {
                        'X-Local-Token': localStorage.getItem('local_token') || ''
                    },
                    body: formData
                });
                if (!requestIsCurrent()) return;
                
                if (!res.ok) {
                    const err = await res.json();
                    if (!requestIsCurrent()) return;
                    throw new Error(err.message || `HTTP ${res.status}`);
                }

                const data = await res.json();
                if (!requestIsCurrent()) return;
                if (data.status === 'success' && data.solution) {
                    q.answer_markdown = data.solution;
                    if (answerTextarea) answerTextarea.value = data.solution;
                    renderParsedCardPreview(card, q.content || '', q.answer_markdown);
                    showToast(`第 ${index + 1} 题 AI 解析生成成功！`, 'success');
                } else {
                    throw new Error(data.message || '生成解答失败');
                }
            } catch (err) {
                if (!requestIsCurrent()) return;
                console.error(err);
                showToast(`生成第 ${index + 1} 题解答失败: ${err.message}`, 'error');
                renderParsedCardPreview(card, q.content || '', q.answer_markdown || '');
            } finally {
                if (requestIsCurrent() && btn) {
                    btn.disabled = false;
                    btn.innerHTML = q.answer_markdown ? '<i class="fa-solid fa-wand-magic-sparkles text-indigo-500"></i><span>重生成解析</span>' : '<i class="fa-solid fa-wand-magic-sparkles text-indigo-500"></i><span>AI 生成解析</span>';
                }
            }
        }

        // 智能并发队列解答生成器
        async function processAsyncAnswerGeneration(questions, generation = parsedQuestionsGeneration) {
            if (!questions || questions.length === 0) return;
            const requestIsCurrent = () => generation === parsedQuestionsGeneration &&
                questions === parsedQuestionsData;
            if (!requestIsCurrent()) return;

            const needAnswersIndices = [];
            questions.forEach((q, idx) => {
                const ans = (q.answer_markdown || '').trim();
                // 仅对未包含解答且未被打上原版提取标记的题目自动推导
                if (!ans || (!ans.includes('[EXTRACTED_ORIGINAL]') && ans.length < 5)) {
                    needAnswersIndices.push(idx);
                }
            });

            if (needAnswersIndices.length === 0) {
                appendImportLog('试卷成功提取到所有原版参考答案/解析，无须额外推导。', 'success');
                return;
            }

            appendImportLog(`已开启 AI 自动解析，正在为 ${needAnswersIndices.length} 道题目并发推导解答步骤 (并发上限: 3)...`, 'info');

            // 对应卡片设置加载排队 UI
            needAnswersIndices.forEach(idx => {
                const card = document.getElementById(`parsed-card-${idx}`);
                if (card) {
                    const answerPrev = card.querySelector('.card-answer-preview');
                    if (answerPrev) {
                        answerPrev.innerHTML = '<div class="flex items-center space-x-1.5 text-indigo-600 font-bold text-[10px] py-1 animate-pulse"><i class="fa-solid fa-spinner animate-spin"></i><span>AI 队列排队中，准备推导解答...</span></div>';
                    }
                }
            });

            // 控制并发池 (Concurrency Limit: 3)
            const MAX_CONCURRENCY = 3;
            let finishedCount = 0;
            let currentPointer = 0;

            async function worker() {
                while (currentPointer < needAnswersIndices.length) {
                    if (!requestIsCurrent()) return;
                    const taskIdx = needAnswersIndices[currentPointer++];
                    const q = questions[taskIdx];
                    if (!q) continue;

                    const card = document.getElementById(`parsed-card-${taskIdx}`);
                    if (card) {
                        const answerPrev = card.querySelector('.card-answer-preview');
                        if (answerPrev) {
                            answerPrev.innerHTML = '<div class="flex items-center space-x-1.5 text-indigo-600 font-bold text-[10px] py-1"><i class="fa-solid fa-brain animate-bounce"></i><span>AI 正在深入推导解答...</span></div>';
                        }
                    }

                    try {
                        const formData = new FormData();
                        formData.append('content', q.content || '');
                        formData.append('question_type', q.question_type || 'detailed_answer');
                        formData.append('stream', 'false');

                        const res = await fetch('/api/ai/solve', {
                            method: 'POST',
                            headers: {
                                'X-Local-Token': localStorage.getItem('local_token') || ''
                            },
                            body: formData
                        });
                        if (!requestIsCurrent()) return;

                        if (res.ok) {
                            const data = await res.json();
                            if (!requestIsCurrent()) return;
                            if (data.status === 'success' && data.solution) {
                                q.answer_markdown = data.solution;
                                finishedCount++;
                                appendImportLog(`[解答进度 ${finishedCount}/${needAnswersIndices.length}] 第 ${taskIdx + 1} 题 AI 解析生成完毕。`, 'success');
                                if (card) {
                                    const answerTextarea = card.querySelector('.card-answer-textarea');
                                    if (answerTextarea) answerTextarea.value = data.solution;
                                    renderParsedCardPreview(card, q.content || '', q.answer_markdown);
                                    const btn = card.querySelector('.card-solve-btn');
                                    if (btn) btn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles text-indigo-500"></i><span>重生成解析</span>';
                                }
                            }
                        }
                    } catch (e) {
                        if (!requestIsCurrent()) return;
                        console.error(`第 ${taskIdx + 1} 题推导解答失败:`, e);
                        if (card) {
                            renderParsedCardPreview(card, q.content || '', q.answer_markdown || '');
                        }
                    }
                }
            }

            const workers = [];
            for (let i = 0; i < Math.min(MAX_CONCURRENCY, needAnswersIndices.length); i++) {
                workers.push(worker());
            }
            await Promise.all(workers);
            if (!requestIsCurrent()) return;
            appendImportLog(`🎉 试卷所有空缺题目（共 ${needAnswersIndices.length} 题）的 AI 解答推导全部完成！`, 'success');
        }

        window.generateSingleAnswer = generateSingleAnswer;
        window.processAsyncAnswerGeneration = processAsyncAnswerGeneration;
        window.associateRelatedQuestion = associateRelatedQuestion;
        window.clearRelatedQuestion = clearRelatedQuestion;
