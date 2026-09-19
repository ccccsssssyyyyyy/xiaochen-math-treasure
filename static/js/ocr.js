        function setupUploadHandlers() {
            // Setup Illustration drag and drop
            const illDropZone = document.getElementById('illustrationDropZone');
            const illFileInput = document.getElementById('illustrationFileInput');
            
            illDropZone.onclick = () => illFileInput.click();
            
            illFileInput.onchange = () => {
                if (illFileInput.files.length > 0) {
                    uploadIllustration(illFileInput.files[0]);
                    illFileInput.value = ''; // Reset so same file can be uploaded again
                }
            };
            
            setupDragDropListeners(illDropZone, (files) => Array.from(files).forEach(f => uploadIllustration(f)));
            setupPasteListener(illDropZone, (file) => uploadIllustration(file));
            setupPasteListener(document.getElementById('editContent'), (file) => uploadIllustration(file));

            // Setup OCR drag and drop
            const ocrDropZone = document.getElementById('ocrDropZone');
            const ocrFileInput = document.getElementById('ocrFileInput');
            
            ocrDropZone.onclick = () => ocrFileInput.click();
            
            ocrFileInput.onchange = () => {
                if (ocrFileInput.files.length > 0) {
                    runOcr(ocrFileInput.files[0]);
                    ocrFileInput.value = ''; // Reset so same file can be uploaded again
                }
            };
            
            setupDragDropListeners(ocrDropZone, (files) => Array.from(files).forEach(f => runOcr(f)));
            setupPasteListener(document.getElementById('ocrDropZone'), (file) => runOcr(file));

            // Setup Question Content OCR file input and Drop Zone
            const contentOcrDropZone = document.getElementById('contentOcrDropZone');
            const contentOcrFileInput = document.getElementById('contentOcrFileInput');
            
            contentOcrDropZone.onclick = () => contentOcrFileInput.click();
            
            contentOcrFileInput.onchange = () => {
                const cf = contentOcrFileInput.files;
                if (cf && cf.length > 1) {
                    runContentOcrBatch(Array.from(cf));
                } else if (cf && cf.length === 1) {
                    runContentOcr(cf[0]);
                }
                contentOcrFileInput.value = ''; // Reset so same file can be uploaded again
            };

            setupDragDropListeners(contentOcrDropZone, (files) => runContentOcrBatch(files));
            setupPasteListener(contentOcrDropZone, (file) => runContentOcr(file));

            // Setup Image Answer Drag and Drop
            const imageAnswerDropZone = document.getElementById('imageAnswerDropZone');
            const imageAnswerFileInput = document.getElementById('imageAnswerFileInput');
            
            if (imageAnswerDropZone) {
                imageAnswerDropZone.onclick = () => imageAnswerFileInput.click();
                
                imageAnswerFileInput.onchange = () => {
                    if (imageAnswerFileInput.files.length > 0) {
                        for (let i = 0; i < imageAnswerFileInput.files.length; i++) {
                            uploadAnswerImage(imageAnswerFileInput.files[i]);
                        }
                        imageAnswerFileInput.value = ''; // Reset so same file can be uploaded again
                    }
                };
                
                setupDragDropListeners(imageAnswerDropZone, (files) => Array.from(files).forEach(f => uploadAnswerImage(f)));
                setupPasteListener(imageAnswerDropZone, (file) => uploadAnswerImage(file));
            }

            // Keyboard accessibility for drag & drop zones (allowing Enter or Space key to trigger file selection)
            [illDropZone, ocrDropZone, contentOcrDropZone, imageAnswerDropZone].forEach(zone => {
                if (zone) {
                    zone.addEventListener('keydown', (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            zone.click();
                        }
                    });
                }
            });

            // Global smart clipboard paste routing for images/screenshots.
            // 方案 E：焦点感知 + 答案区 tab 定向，避免答案截图误入题干 OCR。
            window.addEventListener('paste', (e) => {
                const items = (e.clipboardData || e.originalEvent.clipboardData).items;
                let hasImage = false;
                let imageFile = null;

                for (let index in items) {
                    const item = items[index];
                    if (item.kind === 'file' && item.type.startsWith('image/')) {
                        hasImage = true;
                        imageFile = item.getAsFile();
                        break;
                    }
                }

                if (!(hasImage && imageFile)) return;

                const editAnswerMarkdown = document.getElementById('editAnswerMarkdown');
                const activeEl = document.activeElement;

                // E-1: 焦点在答案文本框 → 按当前光标位置内联插入图片解答
                if (editAnswerMarkdown && activeEl === editAnswerMarkdown) {
                    uploadAnswerImage(imageFile, true);
                    e.preventDefault();
                    return;
                }

                // E-2: 错题审校页打开着 → 这张截图是给「解析截图识别」的。
                //     审校页里没有插图区也没有录入表单，若不在这里截住，会掉进下面的
                //     兜底分支被 uploadIllustration 收进题库录入区，用户回到录入页
                //     时会莫名多出一张插图。焦点落在题面框时只提示、不乱填解析。
                const reviewView = document.getElementById('mistakeReviewView');
                if (reviewView && reviewView.offsetParent !== null) {
                    const reviewContent = document.getElementById('mrContent');
                    if (activeEl && reviewContent && activeEl === reviewContent) {
                        if (typeof window.showToast === 'function') {
                            window.showToast('审校页的截图识别只写解析；题面截图请用「补图形」或「重新识别这道」', 'warning');
                        }
                    } else if (typeof window.runReviewAnswerOcr === 'function') {
                        window.runReviewAnswerOcr(imageFile);
                    }
                    e.preventDefault();
                    return;
                }

                // E-3: 焦点不在文本框时，若答案区的某个 tab 处于激活，则定向到对应答案区 drop zone
                const tabOcr = document.getElementById('tabContent-ocr');
                const tabImage = document.getElementById('tabContent-image');
                const ocrTabActive = !!(tabOcr && tabOcr.offsetParent !== null);
                const imageTabActive = !!(tabImage && tabImage.offsetParent !== null);

                if (imageTabActive) {
                    const zone = document.getElementById('imageAnswerDropZone');
                    if (zone && zone.offsetParent !== null) {
                        uploadAnswerImage(imageFile, false);
                        e.preventDefault();
                        return;
                    }
                } else if (ocrTabActive) {
                    const zone = document.getElementById('ocrDropZone');
                    if (zone && zone.offsetParent !== null) {
                        runOcr(imageFile);
                        e.preventDefault();
                        return;
                    }
                }

                // 兜底：按「离视口中心最近的可见 drop zone」路由（原题干 OCR / 插图行为）
                const targets = [
                    {
                        element: document.getElementById('illustrationDropZone'),
                        handler: (file) => uploadIllustration(file)
                    },
                    {
                        element: document.getElementById('contentOcrDropZone'),
                        handler: (file) => runContentOcrBatch([file])
                    },
                    {
                        element: document.getElementById('ocrDropZone'),
                        handler: (file) => runOcr(file)
                    },
                    {
                        element: document.getElementById('imageAnswerDropZone'),
                        handler: (file) => uploadAnswerImage(file)
                    }
                ];

                // Filter to only get elements that are actually visible on screen
                const visibleTargets = targets.filter(t => {
                    return t.element && t.element.offsetParent !== null;
                });

                if (visibleTargets.length > 0) {
                    // Calculate coordinates of the center of the viewport
                    const viewCenterX = window.innerWidth / 2;
                    const viewCenterY = window.innerHeight / 2;

                    let bestTarget = null;
                    let minDistance = Infinity;

                    visibleTargets.forEach(t => {
                        const rect = t.element.getBoundingClientRect();
                        const centerX = rect.left + rect.width / 2;
                        const centerY = rect.top + rect.height / 2;

                        // Euclidean distance to viewport center
                        const dx = centerX - viewCenterX;
                        const dy = centerY - viewCenterY;
                        const dist = Math.sqrt(dx * dx + dy * dy);

                        if (dist < minDistance) {
                            minDistance = dist;
                            bestTarget = t;
                        }
                    });

                    if (bestTarget) {
                        bestTarget.handler(imageFile);
                        e.preventDefault();
                        return;
                    }
                }

                // Fallback to upload as illustration if no targets are visible
                uploadIllustration(imageFile);
                e.preventDefault();
            });

            // 初始化「沿用为后续默认来源」勾选与来源继承
            (function initSourceDefault() {
                const src = document.getElementById('editSource');
                const chk = document.getElementById('editSourceDefault');
                if (!src || !chk) return;
                chk.checked = _getUseSourceDefault();
                src.addEventListener('input', function () {
                    if (chk.checked) _setSourceDefault(src.value.trim());
                });
                chk.addEventListener('change', function () {
                    if (chk.checked) {
                        _setUseSourceDefault(true);
                        _setSourceDefault(src.value.trim());
                    } else {
                        _setUseSourceDefault(false);
                    }
                });
                applyInheritanceToSource();
            })();

            // 初始化「下一张进题干」临时覆盖 checkbox
            (function initForceQuestionCheckbox() {
                const chk = document.getElementById('contentOcrForceQuestion');
                if (!chk) return;
                chk.checked = contentOcrForceNextToQuestion;
                chk.addEventListener('change', function () {
                    contentOcrForceNextToQuestion = chk.checked;
                });
            })();

            // 初始化「多图合并」开关 UI（与 PDF 裁切 OCR 共用 content 偏好）
            refreshContentOcrModeUI();
        }
        function setupDragDropListeners(zone, onFileReceived) {
            ['dragenter', 'dragover'].forEach(eventName => {
                zone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    zone.classList.add('border-brand-500', 'bg-brand-50/20');
                }, false);
            });
            
            ['dragleave', 'drop'].forEach(eventName => {
                zone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    zone.classList.remove('border-brand-500', 'bg-brand-50/20');
                }, false);
            });
            
            zone.addEventListener('drop', (e) => {
                const dt = e.dataTransfer;
                const files = dt.files;
                if (files && files.length > 0) {
                    onFileReceived(Array.from(files));
                }
            }, false);
        }

        // Paste clipboard screen captures handler
        function setupPasteListener(element, onFileReceived) {
            element.addEventListener('paste', (e) => {
                const items = (e.clipboardData || e.originalEvent.clipboardData).items;
                for (let index in items) {
                    const item = items[index];
                    if (item.kind === 'file') {
                        const blob = item.getAsFile();
                        onFileReceived(blob);
                        e.preventDefault();
                        e.stopPropagation();
                    }
                }
            });
        }

        // Clean and strip leading question numbers and exclamation noise from OCR LaTeX results
        function cleanMathOcrText(text) {
            if (!text) return '';
            
            // 1. Strip LaTeX thin space \!, \, and literal ! / ！
            let cleaned = text.replace(/\\!/g, '');
            cleaned = cleaned.replace(/\\,/g, ''); // Remove all \, thin spaces
             // Protect markdown image starting indicator ![, replace other exclamation marks, and restore
             cleaned = cleaned.replace(/!\[/g, '___MARKDOWN_IMG_START___');
             cleaned = cleaned.replace(/[!！]/g, '');
             cleaned = cleaned.replace(/___MARKDOWN_IMG_START___/g, '![');
            
            // 2. Strip leading question numbers recursively (e.g., "一、 1. " -> "1. " -> "")
            let prev = '';
            while (cleaned !== prev) {
                prev = cleaned;
                cleaned = cleaned.trim();
                
                // Pattern 1: "第 1 题", "第1题", "第1题、" etc.
                cleaned = cleaned.replace(/^第\s*\d+\s*题[\s\.\,，、．\:\：\-\—\~]*/i, '');
                
                // Pattern 2: Chinese numbers "一、", "十一．", etc.
                cleaned = cleaned.replace(/^[一二三四五六七八九十百]+[\s、．\.\,，\:\：\-\—\~]+/i, '');
                
                // Pattern 3: parenthesized or bracketed numbers: (1), （2）, [3], 【4】
                cleaned = cleaned.replace(/^[\(（\[【]\s*\d+\s*[\)）\]】][\s\.\,，、．\:\：\-\—\~]*/i, '');
                
                // Pattern 4: normal digits followed by punctuation: 1., 12、, 3, 4．, etc.
                cleaned = cleaned.replace(/^\d+[\s\.\,，、．\:\：\-\—\~]+/, '');
                
                // Pattern 5: "例 1:", "例题 1:", "例1", "例题1：", etc.
                cleaned = cleaned.replace(/^例(?:题)?\s*\d+[\s\.\,，、．\:\：\-\—\~]*/i, '');
            }
            
            return cleaned.trim();
        }

        let aiSolveRequestSequence = 0;
        let aiSolveCompletionTimer = null;

        function isAiSolveRequestCurrent(sequence, controller, editorSnapshot) {
            return sequence === aiSolveRequestSequence &&
                controller === aiSolveAbortController &&
                !controller.signal.aborted &&
                EditorState.isCurrent(editorSnapshot);
        }

        function resetAiSolveUi(resetProgress = false) {
            const btn = document.getElementById('aiSolveBtn');
            const loader = document.getElementById('aiLoadingIndicator');
            const progressBar = document.getElementById('aiSolveProgressBar');
            if (btn) {
                btn.disabled = false;
                btn.classList.remove('opacity-50', 'pointer-events-none');
            }
            if (loader) loader.classList.add('hidden');
            if (resetProgress && progressBar) progressBar.style.width = '0%';
        }

        function abortActiveAiSolve() {
            const controller = aiSolveAbortController;
            const hadActiveRequest = Boolean(controller || aiSolveCompletionTimer);
            aiSolveRequestSequence += 1;
            aiSolveAbortController = null;
            if (aiSolveCompletionTimer) {
                clearTimeout(aiSolveCompletionTimer);
                aiSolveCompletionTimer = null;
            }
            if (controller) controller.abort();
            if (hadActiveRequest) resetAiSolveUi(true);
            return hadActiveRequest;
        }

        // EditorState calls this one boundary whenever a question, draft or new
        // editor session takes ownership of the form.
        window.invalidateEditorSessionAsyncWork = () => cancelAllOcr(false);

        // Cancel and abort all active OCR processes (both content and answer OCR)
        function cancelAllOcr(clearWhenIdle = true) {
            let aborted = false;
            
            // Handle content OCR abort
            if (contentOcrAbortController) {
                contentOcrAbortController.abort();
                contentOcrAbortController = null;
                aborted = true;
                
                // Hide loading text and update status badge for content OCR preview
                const contentOcrLoadingText = document.getElementById('contentOcrStatusLoadingText');
                const contentOcrStatusBadge = document.getElementById('contentOcrStatusBadge');
                if (contentOcrLoadingText) contentOcrLoadingText.classList.add('hidden');
                if (contentOcrStatusBadge) {
                    contentOcrStatusBadge.classList.remove('hidden');
                    contentOcrStatusBadge.textContent = '已取消识别 (点击可更换图片)';
                }
            }
            
            // Handle answer OCR abort
            if (answerOcrAbortController) {
                answerOcrAbortController.abort();
                answerOcrAbortController = null;
                aborted = true;
                
                // Hide loading text and update status badge for answer OCR preview
                const ocrStatusLoadingText = document.getElementById('ocrStatusLoadingText');
                const ocrStatusBadge = document.getElementById('ocrStatusBadge');
                if (ocrStatusLoadingText) ocrStatusLoadingText.classList.add('hidden');
                if (ocrStatusBadge) {
                    ocrStatusBadge.classList.remove('hidden');
                    ocrStatusBadge.textContent = '已取消识别 (点击可更换图片)';
                }
            }

            // Handle AI solve abort
            if (abortActiveAiSolve()) {
                aborted = true;
            }
            
            if (aborted) {
                // Restore loading indicator and dropzone UI states (but keep image preview)
                const contentOcrDropZone = document.getElementById('contentOcrDropZone');
                const contentOcrLoading = document.getElementById('contentOcrLoadingIndicator');
                if (contentOcrDropZone && contentOcrLoading) {
                    contentOcrLoading.classList.add('hidden');
                    contentOcrDropZone.classList.remove('hidden');
                }
                
                const ocrDropZone = document.getElementById('ocrDropZone');
                const ocrLoading = document.getElementById('ocrLoadingIndicator');
                if (ocrDropZone && ocrLoading) {
                    ocrLoading.classList.add('hidden');
                    ocrDropZone.classList.remove('hidden');
                }
                
                showToast('OCR 识别已取消，按 ESC 可清除图片', 'info');
            } else if (clearWhenIdle) {
                // If no active OCR process is running, clear all image previews and results completely
                clearContentOcrPreview();
                clearOcrPreview();
                showToast('已清除当前识图状态与图片', 'info');
            }
        }

        // Global Esc key listener for canceling OCR & closing lightbox
        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' || e.key === 'Esc') {
                const lightbox = document.getElementById('imageLightbox');
                if (lightbox && !lightbox.classList.contains('hidden')) {
                    closeLightbox();
                } else {
                    cancelAllOcr();
                }
            }
        });

        // 1. Upload Illustration handler
        function uploadIllustration(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const formData = new FormData();
            formData.append('file', file);
            const editorSession = EditorState.snapshot();
            
            showToast('正在上传插图...', 'info');
            
            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (!EditorState.isCurrent(editorSession)) return;
                if (data.status === 'success') {
                    showToast('图片上传成功！');
                    uploadedImages.push(data.file_path);
                    renderIllustrationBadges();
                    
                    // Insert image markdown tag into textarea where cursor is
                    insertImageTag(data.file_path);
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (!EditorState.isCurrent(editorSession)) return;
                showToast('上传图片出错: ' + err, 'error');
            });
        }

        function insertImageTag(filePath) {
            const textarea = document.getElementById('editContent');
            const markdownTag = `\n\n![插图](${filePath})\n\n`;
            
            const startPos = textarea.selectionStart;
            const endPos = textarea.selectionEnd;
            const originalVal = textarea.value;
            
            textarea.value = originalVal.substring(0, startPos) + markdownTag + originalVal.substring(endPos);
            
            // Dispatch input event to refresh preview
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            
            // Put cursor right after inserted image
            const newCursorPos = startPos + markdownTag.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
        }

        function renderIllustrationBadges() {
            const listContainer = document.getElementById('illustrationsList');
            if (!listContainer) return;
            listContainer.innerHTML = '';
            
            uploadedImages.forEach((path, idx) => {
                const filename = path.split('/').pop();
                listContainer.innerHTML += `
                    <div class="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border bg-white shadow-sm text-xs text-slate-600">
                        <i class="fa-solid fa-file-image text-brand-500"></i>
                        <span class="truncate max-w-[100px]" title="${filename}">${filename}</span>
                        <button type="button" onclick="deleteUploadedIllustration(${idx})" class="text-slate-400 hover:text-red-500 transition-all font-semibold pl-1">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                `;
            });
            
            // Toggle Content TikZ Panel visibility based on original illustrations or existing code
            const contentTikzContainer = document.getElementById('contentTikzContainer');
            if (contentTikzContainer) {
                const hasOriginalImage = uploadedImages.some(path => !path.includes('/tikz_'));
                const hasTikzCode = document.getElementById('editContentTikzCode') && document.getElementById('editContentTikzCode').value.trim();
                if (hasOriginalImage || hasTikzCode) {
                    contentTikzContainer.classList.remove('hidden');
                } else {
                    contentTikzContainer.classList.add('hidden');
                }
            }
        }

        function deleteUploadedIllustration(idx) {
            // We just remove it from active images array
            const deletedPath = uploadedImages[idx];
            uploadedImages.splice(idx, 1);
            renderIllustrationBadges();
            
            // Remove markdown code from editor if user wants
            const textarea = document.getElementById('editContent');
            textarea.value = textarea.value.replace(new RegExp(`\\!\\[插图\\]\\(${deletedPath}\\)`, 'g'), '');
            textarea.dispatchEvent(new Event('input'));
            
            showToast('插图已移除');
        }

        // 1.5 Image Answer (No OCR) handler
        // atCaret=true 表示来自答案文本框内的光标插入（内联），false 表示来自 drop zone / tab 定向（追加到末尾）
        function uploadAnswerImage(file, atCaret) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }

            const formData = new FormData();
            formData.append('file', file);
            const editorSession = EditorState.snapshot();

            // 上传是异步的，先在上传前锁定答案文本框的光标位置，避免上传期间焦点漂移导致插入错位
            let caretPos = null;
            if (atCaret) {
                const ta = document.getElementById('editAnswerMarkdown');
                if (ta) caretPos = ta.selectionStart;
            }

            showToast('正在上传图片解答...', 'info');

            fetch('/api/upload', {
                method: 'POST',
                body: formData
            })
            .then(r => r.json())
            .then(data => {
                if (!EditorState.isCurrent(editorSession)) return;
                if (data.status === 'success') {
                    showToast('图片解答上传成功！');
                    insertAnswerImageTag(data.file_path, caretPos);
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (!EditorState.isCurrent(editorSession)) return;
                showToast('上传图片出错: ' + err, 'error');
            });
        }

        // caretPos 为数字时按该光标位置插入（内联）；为 null/undefined 时追加到文末
        function insertAnswerImageTag(filePath, caretPos) {
            const textarea = document.getElementById('editAnswerMarkdown');
            const originalVal = textarea.value;

            let startPos, endPos;
            if (typeof caretPos === 'number' && caretPos >= 0 && caretPos <= originalVal.length) {
                startPos = endPos = caretPos;       // 内联：用上传前锁定的光标位置
            } else {
                startPos = endPos = originalVal.length;  // 追加：落到文末
            }

            let markdownTag;
            if (startPos >= originalVal.length) {
                // 追加到末尾：与原有行为一致，图文之间保留空行分隔
                markdownTag = `\n\n![图片解答](${filePath})\n\n`;
            } else {
                // 内联插入到文字中间：用空格包裹，不强制换行，避免破坏段落
                const beforeChar = originalVal.charAt(startPos - 1);
                const afterChar = originalVal.charAt(startPos);
                const pre = (beforeChar && !/\s/.test(beforeChar)) ? ' ' : '';
                const post = (afterChar && !/\s/.test(afterChar)) ? ' ' : '';
                markdownTag = `${pre}![图片解答](${filePath})${post}`;
            }

            textarea.value = originalVal.substring(0, startPos) + markdownTag + originalVal.substring(endPos);

            // Dispatch input event to refresh preview
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();

            // Put cursor right after inserted image
            const newCursorPos = startPos + markdownTag.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);

            // Sync answer images array & badges
            syncAnswerImagesFromMarkdown();
        }

        function renderAnswerImageBadges() {
            const listContainer = document.getElementById('imageAnswersList');
            if (!listContainer) return;
            
            listContainer.innerHTML = '';
            
            if (uploadedAnswerImages.length === 0) {
                listContainer.innerHTML = '<p id="noImageAnswerPlaceholder" class="text-xs text-slate-400 italic w-full text-center py-2">暂无已上传的图片解答</p>';
                return;
            }
            
            uploadedAnswerImages.forEach((path, idx) => {
                const filename = path.split('/').pop();
                listContainer.innerHTML += `
                    <div class="flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg border bg-white shadow-sm text-xs text-slate-600">
                        <i class="fa-solid fa-file-image text-brand-500"></i>
                        <span class="truncate max-w-[100px]" title="${filename}">${filename}</span>
                        <button type="button" onclick="deleteUploadedAnswerImage(${idx})" class="text-slate-400 hover:text-red-500 transition-all font-semibold pl-1">
                            <i class="fa-solid fa-xmark"></i>
                        </button>
                    </div>
                `;
            });
        }

        function deleteUploadedAnswerImage(idx) {
            const deletedPath = uploadedAnswerImages[idx];
            const textarea = document.getElementById('editAnswerMarkdown');
            
            // Remove markdown code from editor
            const escaped = escapeRegExp(deletedPath);
            textarea.value = textarea.value.replace(new RegExp(`\\\\!\\\\\\[.*?\\\\\\]\\\\(${escaped}\\\\)`, 'g'), '');
            textarea.value = textarea.value.replace(new RegExp(`\\!\\[.*?\\]\\(${escaped}\\)`, 'g'), '');
            textarea.dispatchEvent(new Event('input'));
            
            showToast('图片解答已从解析中移除');
            
            // Sync badges
            syncAnswerImagesFromMarkdown();
        }

        function syncAnswerImagesFromMarkdown() {
            const val = document.getElementById('editAnswerMarkdown').value || '';
            const regex = /!\[.*?\]\((.*?)\)/g;
            let match;
            const foundImages = [];
            while ((match = regex.exec(val)) !== null) {
                if (match[1] && match[1].includes('/static/uploads/')) {
                    foundImages.push(match[1]);
                }
            }
            uploadedAnswerImages = foundImages;
            renderAnswerImageBadges();
        }

        // 2. OCR Answer screenshot handler
        function updateOcrPlaceholder(type) {
            const getEngineLabel = (val) => {
                if (val === 'siliconflow') {
                    return "SiliconFlow 硅基流动云端";
                } else if (val === 'ali_bailian') {
                    return "阿里百炼";
                }
                return val || "";
            };

            const label = `当前引擎: ${getEngineLabel(systemPreferEngine)}`;

            if (type === 'content') {
                const subText = document.getElementById('contentOcrPlaceholderSub');
                if (subText) {
                    subText.textContent = label;
                }
            } else if (type === 'answer') {
                const subText = document.getElementById('answerOcrPlaceholderSub');
                if (subText) {
                    subText.textContent = label;
                }
            }
        }

        function runOcr(file) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const ocrDropZone = document.getElementById('ocrDropZone');
            const ocrOutput = document.getElementById('ocrOutputBox');
            const ocrResult = document.getElementById('ocrResultText');
            const ocrConf = document.getElementById('ocrConfBadge');
            
            const previewImg = document.getElementById('ocrPreviewImg');
            const previewContainer = document.getElementById('ocrPreviewContainer');
            const placeholder = document.getElementById('ocrPlaceholder');
            const statusBadge = document.getElementById('ocrStatusBadge');
            const loadingText = document.getElementById('ocrStatusLoadingText');
            
            // Read file to show image preview in the upload area IMMEDIATELY
            const reader = new FileReader();
            reader.onload = (e) => {
                if (previewImg && previewContainer && placeholder) {
                    previewImg.src = e.target.result;
                    placeholder.classList.add('hidden');
                    previewContainer.classList.remove('hidden');
                    
                    if (statusBadge) statusBadge.classList.add('hidden');
                    if (loadingText) loadingText.classList.remove('hidden');
                }
            };
            reader.readAsDataURL(file);
            
            ocrOutput.classList.add('hidden');
            
            // Abort previous running controller if any
            if (answerOcrAbortController) {
                answerOcrAbortController.abort();
            }
            answerOcrAbortController = new AbortController();
            const signal = answerOcrAbortController.signal;
            
            const engine = 'default';
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('engine', engine);
            
            fetch('/api/ocr', {
                method: 'POST',
                body: formData,
                signal: signal
            })
            .then(r => r.json())
            .then(data => {
                if (loadingText) loadingText.classList.add('hidden');
                if (statusBadge) {
                    statusBadge.classList.remove('hidden');
                    statusBadge.textContent = '已加载截图预览 (点击可更换图片)';
                }
                
                answerOcrAbortController = null;
                
                if (data.status === 'success') {
                    showToast('OCR 识别成功并已自动填入！');
                    ocrOutput.classList.remove('hidden');
                    ocrResult.textContent = cleanMathOcrText(data.latex);
                    ocrConf.textContent = `置信度: ${(data.confidence * 100).toFixed(1)}%`;
                    
                    if (data.image_path) {
                        window.lastOcrOriginalImagePath = data.image_path;
                    }
                    
                    // Automatically load OCR results into final review editor silently
                    loadToFinalReview('ocr');
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') {
                    return; // Gracefully handle manual aborts without error toast
                }
                if (loadingText) loadingText.classList.add('hidden');
                if (statusBadge) {
                    statusBadge.classList.remove('hidden');
                    statusBadge.textContent = '已加载截图预览 (点击可更换图片)';
                }
                
                answerOcrAbortController = null;
                showToast('OCR 识别出错: ' + err, 'error');
            });
        }

        // ===== 单题录入「当前卷名（默认来源）」会话级继承 =====
        // 勾选「沿用为后续默认来源」后，当前来源即成为默认卷名，之后每道单题
        // （OCR 成功 / 新建题目）自动继承；AI 识别来源、文件名、OCR 卷头任一命中
        // 也可在首次录题时自动设为默认卷名。
        const SOURCE_DEFAULT_KEY = 'mathbank_source_default';
        const SOURCE_USE_KEY = 'mathbank_source_use_default';

        function _getSourceDefault() {
            try { return localStorage.getItem(SOURCE_DEFAULT_KEY) || ''; } catch (e) { return ''; }
        }
        function _getUseSourceDefault() {
            try { return localStorage.getItem(SOURCE_USE_KEY) === '1'; } catch (e) { return false; }
        }
        function _setSourceDefault(val) {
            try {
                if (val) localStorage.setItem(SOURCE_DEFAULT_KEY, val);
                else localStorage.removeItem(SOURCE_DEFAULT_KEY);
            } catch (e) {}
        }
        function _setUseSourceDefault(flag) {
            try {
                if (flag) localStorage.setItem(SOURCE_USE_KEY, '1');
                else localStorage.removeItem(SOURCE_USE_KEY);
            } catch (e) {}
        }

        // 把默认卷名应用到「来源」输入框（继承）
        function applyInheritanceToSource() {
            const src = document.getElementById('editSource');
            const chk = document.getElementById('editSourceDefault');
            if (!src) return;
            if (_getUseSourceDefault()) {
                const def = _getSourceDefault();
                if (def) src.value = def;
                if (chk) chk.checked = true;
            }
        }

        // 从 OCR 文本 / 文件名 推测卷名候选
        function detectSourceCandidate(text, fileName) {
            if (text) {
                const lines = text.split(/\r?\n/).map(function (s) { return s.trim(); })
                    .filter(function (s) { return s.length > 0; }).slice(0, 3);
                const headRe = /(学校|中学|学院|大学|附属|届|学年|学期|期中|期末|月考|模拟|联考|真题|试卷|考试|调研|测验|诊断|三模|二模|一模|统考)/;
                const bracketRe = /[（(]([^（）()]{4,40})[)）]/;
                const notQuestionRe = /(已知|求|证明|下列|如图|计算|若|设|解|选择|填空)/;
                for (let i = 0; i < lines.length; i++) {
                    const ln = lines[i];
                    const bm = ln.match(bracketRe);
                    if (bm && !notQuestionRe.test(bm[1])) return bm[1];
                    if (ln.length <= 40 && headRe.test(ln) && !/[=＝]/.test(ln) && !/[?？]$/.test(ln)) {
                        return ln;
                    }
                }
            }
            if (fileName) {
                let n = String(fileName).replace(/\.[^.]+$/, '');
                n = n.replace(/^(微信图片|image|img|screenshot|截图|未命名|微信|qqimg|mmexport|weixin)/i, '');
                n = n.replace(/[_\s]?\d{8,}|[_\s]?\d{6,}/g, '');
                n = n.trim();
                if (n.length >= 3 && n.length <= 40) return n;
            }
            return '';
        }

        window.detectSourceCandidate = detectSourceCandidate;
        window.applyInheritanceToSource = applyInheritanceToSource;

        // 2.2 OCR Question Content screenshot handler
        function runContentOcr(file, opts) {
            if (!file.type.startsWith('image/')) {
                showToast('请上传有效的图片格式！', 'error');
                return;
            }
            
            const contentOcrDropZone = document.getElementById('contentOcrDropZone');
            const contentOcrOutput = document.getElementById('contentOcrOutputBox');
            const contentOcrResult = document.getElementById('contentOcrResultText');
            const contentOcrConf = document.getElementById('contentOcrConfBadge');
            
            const previewImg = document.getElementById('contentOcrPreviewImg');
            const previewContainer = document.getElementById('contentOcrPreviewContainer');
            const placeholder = document.getElementById('contentOcrPlaceholder');
            const statusBadge = document.getElementById('contentOcrStatusBadge');
            const loadingText = document.getElementById('contentOcrStatusLoadingText');
            
            // 1. Read file to show image preview in the upload area
            const reader = new FileReader();
            reader.onload = (e) => {
                if (previewImg && previewContainer && placeholder) {
                    previewImg.src = e.target.result;
                    placeholder.classList.add('hidden');
                    previewContainer.classList.remove('hidden');
                    
                    if (statusBadge) statusBadge.classList.add('hidden');
                    if (loadingText) loadingText.classList.remove('hidden');
                }
            };
            reader.readAsDataURL(file);
            
            contentOcrOutput.classList.add('hidden');
            
            // Abort previous running controller if any
            if (contentOcrAbortController) {
                contentOcrAbortController.abort();
            }
            contentOcrAbortController = new AbortController();
            const signal = contentOcrAbortController.signal;
            
            const engine = 'default';
            
            const formData = new FormData();
            formData.append('file', file);
            formData.append('engine', engine);
            
            return fetch('/api/ocr', {
                method: 'POST',
                body: formData,
                signal: signal
            })
            .then(r => r.json())
            .then(data => {
                if (loadingText) loadingText.classList.add('hidden');
                if (statusBadge) {
                    statusBadge.classList.remove('hidden');
                    statusBadge.textContent = '已加载截图预览 (点击可更换图片)';
                }
                
                contentOcrAbortController = null;
                
                if (data.status === 'success') {
                    showToast('题干 OCR 识别成功并已自动填入！');
                    contentOcrOutput.classList.remove('hidden');
                    
                    // 1. Clean LaTeX noise, exclamation marks and leading question numbers
                    const cleanLatex = cleanMathOcrText(data.latex);
                    contentOcrResult.textContent = cleanLatex;
                    contentOcrConf.textContent = `置信度: ${(data.confidence * 100).toFixed(1)}%`;
                    
                    if (data.image_path) {
                        window.lastOcrOriginalImagePath = data.image_path;
                    }
                    
                    // 2. Automatically load results into the persistent content editor
                    const _isAppend = !!(opts && opts.isAppend);
                    const _forceAnswer = !!(opts && opts.forceAnswer);
                    loadToContentEditor('ocr', _isAppend, _forceAnswer);

                    // 多图批处理时跳过单次收尾（统一在 runContentOcrBatch 末尾 finalize 一次）
                    if (!(opts && opts.skipFinalize)) {
                        finalizeContentOcr(file);
                    }
                } else {
                    showToast(data.message, 'error');
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') {
                    return; // Gracefully handle manual aborts without error toast
                }
                if (loadingText) loadingText.classList.add('hidden');
                if (statusBadge) {
                    statusBadge.classList.remove('hidden');
                    statusBadge.textContent = '已加载截图预览 (点击可更换图片)';
                }
                
                contentOcrAbortController = null;
                showToast('题干 OCR 识别出错: ' + err, 'error');
            });
        }

        // 单张 OCR 成功后的收尾：来源继承/首次自动设卷名 + 仅做一次 AI 分类
        function finalizeContentOcr(firstFile) {
            const srcEl = document.getElementById('editSource');
            if (srcEl) {
                if (_getUseSourceDefault() && _getSourceDefault()) {
                    applyInheritanceToSource(); // 后续题目：直接继承
                } else if (!srcEl.value.trim()) {
                    // 从已合并的题干全文 + 首张文件名推测卷名
                    const combined = document.getElementById('editContent').value || '';
                    const cand = detectSourceCandidate(combined, firstFile && firstFile.name);
                    if (cand) {
                        srcEl.value = cand;
                        _setUseSourceDefault(true);
                        _setSourceDefault(cand);
                        const chk = document.getElementById('editSourceDefault');
                        if (chk) chk.checked = true;
                    }
                }
            }
            autoClassifyFromContent();
        }

        // 内容 OCR 队列：顺序消费，避免连续粘贴时后一张 abort 前一张
        let contentOcrQueue = [];
        let isContentOcrProcessing = false;
        // 答案识别后，临时强制下一张图进题干（checkbox 状态）
        let contentOcrForceNextToQuestion = false;

        // 多张截图合并：依次 OCR 写入，全部完成后再统一 finalize（仅一次分类 + 来源识别）
        async function runContentOcrBatch(files) {
            const arr = Array.isArray(files) ? files : Array.from(files || []);
            if (!arr.length) return;
            contentOcrQueue.push(...arr);
            if (isContentOcrProcessing) return;
            isContentOcrProcessing = true;
            try {
                await processContentOcrQueue();
            } finally {
                isContentOcrProcessing = false;
            }
        }

        async function processContentOcrQueue() {
            const mode = (typeof window.getOcrMode === 'function') ? window.getOcrMode('content') : 'replace';
            let firstFile = null;
            let firstInRound = true;
            let hasAnswerStarted = false;
            while (contentOcrQueue.length > 0) {
                const file = contentOcrQueue.shift();
                if (!firstFile) firstFile = file;
                // 替换模式下本轮首张覆盖旧内容，其余追加；追加模式下全部追加
                const isAppend = (mode === 'append') || !firstInRound;
                // 一旦本轮已识别出答案，后续图片默认进答案栏；用户可勾选「下一张进题干」临时覆盖
                const forceAnswer = hasAnswerStarted && !contentOcrForceNextToQuestion;
                await runContentOcr(file, { isAppend: isAppend, skipFinalize: true, forceAnswer: forceAnswer });
                // 识别完成后检查答案栏是否有内容
                const answerTextarea = document.getElementById('editAnswerMarkdown');
                hasAnswerStarted = hasAnswerStarted || !!(answerTextarea && answerTextarea.value.trim());
                // 使用了「下一张进题干」覆盖后自动复位
                if (contentOcrForceNextToQuestion) {
                    contentOcrForceNextToQuestion = false;
                    const chk = document.getElementById('contentOcrForceQuestion');
                    if (chk) chk.checked = false;
                }
                firstInRound = false;
            }
            finalizeContentOcr(firstFile);
        }

        // 同步内容 OCR 拖拽区的「多图合并」开关 UI（与 PDF 裁切 OCR 共用 content 偏好）
        function refreshContentOcrModeUI() {
            const mode = (typeof window.getOcrMode === 'function') ? window.getOcrMode('content') : 'replace';
            const rep = document.getElementById('contentOcrModeReplace');
            const app = document.getElementById('contentOcrModeAppend');
            const onCls = 'px-2 py-1 rounded text-[10px] font-bold transition-colors bg-brand-600 text-white';
            const offCls = 'px-2 py-1 rounded text-[10px] font-bold transition-colors bg-slate-200 text-slate-700 hover:bg-slate-300';
            if (rep) rep.className = (mode === 'replace') ? onCls : offCls;
            if (app) app.className = (mode === 'append') ? onCls : offCls;
        }
            window.refreshContentOcrModeUI = refreshContentOcrModeUI;
            window.runContentOcrBatch = runContentOcrBatch;

        // 题干 OCR 成功后自动触发 AI 分类并填充录入表单（无弹窗，覆盖式填充，用户可手改）
        function autoClassifyFromContent() {
            const content = document.getElementById('editContent').value.trim();
            if (!content) return;
            const formData = new FormData();
            formData.append('content', content);
            formData.append('use_free_model', 'true');
            // 学科决定喂给 AI 的目录树：物理挂教科版、化学挂人教版。
            formData.append('subject', window.bankSubject || 'math');
            fetch('/api/ai/classify', { method: 'POST', body: formData })
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'success') {
                        if (typeof window.applyClassifyResultToEditor === 'function') {
                            window.applyClassifyResultToEditor(data);
                        }
                        // 来源继承优先于 AI 单次识别；AI 来源也可首次自动设卷名
                        const srcEl = document.getElementById('editSource');
                        if (_getUseSourceDefault() && _getSourceDefault()) {
                            if (srcEl) srcEl.value = _getSourceDefault();
                        } else if (data.source && srcEl && !srcEl.value.trim()) {
                            srcEl.value = (typeof normalizeSource === 'function') ? normalizeSource(data.source) : data.source;
                            _setUseSourceDefault(true);
                            _setSourceDefault(data.source);
                            const chk = document.getElementById('editSourceDefault');
                            if (chk) chk.checked = true;
                        }
                    } else {
                        showToast(data.message || 'AI 自动分类失败，请手动填写分类', 'info');
                    }
                })
                .catch(() => {
                    showToast('AI 自动分类失败，请手动填写分类', 'info');
                });
        }

        // Switch Question Content workflow tab - Apple Glass Style
        function switchContentTab(tabId) {
            const tabs = ['ocr', 'manual'];
            tabs.forEach(t => {
                const btn = document.getElementById(`contentTabBtn-${t}`);
                const content = document.getElementById(`contentTabContent-${t}`);

                if (t === tabId) {
                    btn.className = "glass-tab-item active flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 text-brand-600";
                    content.classList.remove('hidden');
                } else {
                    btn.className = "glass-tab-item flex-1 py-2 px-3 rounded-lg font-medium text-xs flex items-center justify-center space-x-1.5 text-slate-600";
                    content.classList.add('hidden');
                }
            });
        }

        // Split OCR text by the earliest answer marker.
        //  - First-pass: search literal markers (【答案】/[答案]/<答案> etc.) anywhere in the text.
        //  - Second-pass (colon variants): only count "答案：" / "解析：" etc. when they appear in the
        //    back 2/3 of the text, so a stray "答案是 A." inside the question stem does not trigger a split.
        //  - If nothing matches, return the whole text as the question (backwards compatible).
        function splitOcrTextByAnswerMarker(text) {
            if (!text || !text.trim()) {
                return { question: text || '', answer: '' };
            }

            const anchorReList = [
                /【\s*答\s*案\s*】/g,
                /【\s*解\s*析\s*】/g,
                /【\s*分\s*析\s*】/g,
                /【\s*详\s*解\s*】/g,
                /【\s*解\s*答\s*】/g,
                /【\s*点\s*评\s*】/g,
                /【\s*点\s*睛\s*】/g,
                /\[\s*答\s*案\s*\]/g,
                /\[\s*解\s*析\s*\]/g,
                /\[\s*分\s*析\s*\]/g,
                /\[\s*详\s*解\s*\]/g,
                /\[\s*解\s*答\s*\]/g,
            ];

            const colonReList = [
                /(^|\n)\s*答\s*案\s*[：:]/g,
                /(^|\n)\s*解\s*析\s*[：:]/g,
                /(^|\n)\s*分\s*析\s*[：:]/g,
                /(^|\n)\s*详\s*解\s*[：:]/g,
                /(^|\n)\s*解\s*答\s*[：:]/g,
                /(^|\n)\s*点\s*评\s*[：:]/g,
                /(^|\n)\s*点\s*睛\s*[：:]/g,
            ];

            const len = text.length;
            const colonThreshold = Math.floor(len / 3); // 仅在文本中后段（>=2/3）出现的弱锚点才算
            let bestIdx = -1;

            for (const re of anchorReList) {
                re.lastIndex = 0;
                let m;
                while ((m = re.exec(text)) !== null) {
                    if (bestIdx === -1 || m.index < bestIdx) {
                        bestIdx = m.index;
                    }
                }
            }

            if (bestIdx === -1 || bestIdx < colonThreshold) {
                for (const re of colonReList) {
                    re.lastIndex = 0;
                    let m;
                    while ((m = re.exec(text)) !== null) {
                        const after = m.index + m[1].length; // 跳过行首/换行捕获组
                        if (after < colonThreshold) continue;
                        if (bestIdx === -1 || after < bestIdx) {
                            bestIdx = after;
                        }
                    }
                }
            }

            if (bestIdx === -1) {
                // 调试辅助：当 OCR 文本中找不到任何锚点时，把清洗后的文本前 200 字打到 console，
                // 便于判断到底是「OCR 没识别到答案/解析」还是「锚点没匹配」。生产环境保留 console.warn，
                // 用户在浏览器 F12 Console 看到能直接告诉我们。
                console.warn('[OCR split] 未识别到答案/解析标记。OCR 文本前 200 字:', text.slice(0, 200));
                return { question: text, answer: '' };
            }

            return {
                question: text.slice(0, bestIdx).replace(/\s+$/, ''),
                answer: text.slice(bestIdx).replace(/^\s+/, '')
            };
        }
            
        // Load content OCR result into the persistent editor textarea
        function loadToContentEditor(source, isAppend = false, forceAnswer = false) {
            const textarea = document.getElementById('editContent');
            let contentToImport = '';
            let answerPart = '';
            let didSplit = false;
            let questionWritten = false;
            
                        
                if (source === 'ocr') {
                const rawOcrText = document.getElementById('contentOcrResultText').textContent;
                console.log('[OCR split] loadToContentEditor called with source=ocr, rawText length=' + rawOcrText.length + ', preview: ' + JSON.stringify(rawOcrText.slice(0, 60)) + ', forceAnswer=' + forceAnswer);
                const _split = splitOcrTextByAnswerMarker(rawOcrText);
                contentToImport = _split.question;
                answerPart = _split.answer;
                didSplit = answerPart.length > 0;
                console.log('[OCR split] result: didSplit=' + didSplit + ', question.length=' + contentToImport.length + ', answer.length=' + answerPart.length);

                // 1. Auto-detect if it's a choice question with options A, B, C, D (question part only)
                if (!forceAnswer) {
                    const hasA = /[\s,，、]*\bA(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                    const hasB = /[\s,，、]*\bB(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                    const hasC = /[\s,，、]*\bC(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);
                    const hasD = /[\s,，、]*\bD(?:[\.\s、，．]+|\b|\))/i.test(contentToImport);

                    if (hasA && hasB && hasC && hasD) {
                        const editQType = document.getElementById('editQType');
                        if (editQType) {
                            editQType.value = 'single_choice';
                            editQType.dispatchEvent(new Event('change'));
                        }
                    }
                }

                // 2. Automatically format the OCR content to break choice options onto separate lines beautifully
                contentToImport = formatQuestionContent(contentToImport);
            }
            
            if (!contentToImport.trim() && !answerPart.trim()) {
                showToast('导入内容为空！', 'error');
                return;
            }
            
            // 强制路由到答案栏（答案识别后，后续图片默认进答案栏）
            if (forceAnswer) {
                const answerTextarea = document.getElementById('editAnswerMarkdown');
                if (answerTextarea) {
                    let fullAnswer = '';
                    if (contentToImport.trim() && answerPart.trim()) {
                        fullAnswer = contentToImport.trim() + '\n' + answerPart.trim();
                    } else {
                        fullAnswer = contentToImport.trim() || answerPart.trim();
                    }
                    if (answerTextarea.value.trim()) {
                        answerTextarea.value += '\n\n' + fullAnswer;
                    } else {
                        answerTextarea.value = fullAnswer;
                    }
                    answerTextarea.dispatchEvent(new Event('input'));
                    answerTextarea.classList.add('ring-2', 'ring-emerald-400', 'ring-offset-2');
                    setTimeout(() => answerTextarea.classList.remove('ring-2', 'ring-emerald-400', 'ring-offset-2'), 1200);
                    showToast('已追加至答案栏');
                }
                return;
            }
            
            if (contentToImport.trim()) {
                if (isAppend) {
                    if (textarea.value.trim()) {
                        textarea.value += '\n' + contentToImport;
                    } else {
                        textarea.value = contentToImport;
                    }
                } else {
                    // 替换模式：直接覆盖（与「多图合并」开关语义一致，不再二次确认）
                    textarea.value = contentToImport;
                }
                // Refresh previews
                textarea.dispatchEvent(new Event('input'));
                questionWritten = true;
            }
            
            // 3. 智能拆分：把答案/解析写入答案栏（仅当 OCR 含答案标记时）
            if (didSplit) {
                const answerTextarea = document.getElementById('editAnswerMarkdown');
                console.log('[OCR split] writing answer: textarea-found=' + !!answerTextarea + ', existingLength=' + (answerTextarea ? answerTextarea.value.length : 'N/A') + ', answerPartLength=' + answerPart.length);
                if (answerTextarea) {
                    if (answerTextarea.value.trim()) {
                        // 多图合并：答案累加，不再二次确认
                        answerTextarea.value += '\n\n' + answerPart;
                    } else {
                        answerTextarea.value = answerPart;
                    }
                    answerTextarea.dispatchEvent(new Event('input'));
                    // 闪烁高亮：让答案栏被填入这件事肉眼可见
                    answerTextarea.classList.add('ring-2', 'ring-emerald-400', 'ring-offset-2');
                    setTimeout(() => answerTextarea.classList.remove('ring-2', 'ring-emerald-400', 'ring-offset-2'), 1200);
                    console.log('[OCR split] answer write complete, editAnswerMarkdown.value.length=' + answerTextarea.value.length);
                } else {
                    console.warn('[OCR split] CRITICAL: editAnswerMarkdown element NOT FOUND');
                }
            }
            
            // 4. Toast
            if (didSplit) {
                showToast(questionWritten ? `已自动拆分：题干 ${contentToImport.length} 字 + 答案 ${answerPart.length} 字` : `已自动拆分：答案 ${answerPart.length} 字（题干为空）`);
            } else {
                showToast('已载入至题干编辑框！');
            }
        }

        // Clear OCR image preview and OCR result box
        function clearContentOcrPreview() {
            const previewContainer = document.getElementById('contentOcrPreviewContainer');
            const placeholder = document.getElementById('contentOcrPlaceholder');
            const previewImg = document.getElementById('contentOcrPreviewImg');
            const contentOcrOutput = document.getElementById('contentOcrOutputBox');
            
            if (previewContainer && placeholder && previewImg && contentOcrOutput) {
                previewImg.src = '';
                previewContainer.classList.add('hidden');
                placeholder.classList.remove('hidden');
                contentOcrOutput.classList.add('hidden');
            }
        }

        // Clear Answer OCR image preview and result box
        function clearOcrPreview() {
            const previewContainer = document.getElementById('ocrPreviewContainer');
            const placeholder = document.getElementById('ocrPlaceholder');
            const previewImg = document.getElementById('ocrPreviewImg');
            const ocrOutput = document.getElementById('ocrOutputBox');
            
            if (previewContainer && placeholder && previewImg && ocrOutput) {
                previewImg.src = '';
                previewContainer.classList.add('hidden');
                placeholder.classList.remove('hidden');
                ocrOutput.classList.add('hidden');
            }
        }

        // Lightbox Zoom Functions
        function zoomImage(src) {
            const lightbox = document.getElementById('imageLightbox');
            const lightboxImg = document.getElementById('lightboxImg');
            if (lightbox && lightboxImg) {
                lightboxImg.src = src;
                lightbox.classList.remove('hidden');
                window.MathBankModal.open(lightbox, { onEscape: closeLightbox });
                // Force reflow for transitions
                lightbox.offsetHeight;
                lightbox.classList.remove('opacity-0');
                lightboxImg.classList.remove('scale-95');
                lightboxImg.classList.add('scale-100');
            }
        }

        function closeLightbox() {
            const lightbox = document.getElementById('imageLightbox');
            const lightboxImg = document.getElementById('lightboxImg');
            if (lightbox && lightboxImg) {
                window.MathBankModal.close(lightbox);
                lightbox.classList.add('opacity-0');
                lightboxImg.classList.remove('scale-100');
                lightboxImg.classList.add('scale-95');
                setTimeout(() => {
                    lightbox.classList.add('hidden');
                    lightboxImg.src = '';
                }, 300);
            }
        }

        // Quick math inserting helper
        function insertContentHelper(code) {
            const textarea = document.getElementById('editContent');
            const startPos = textarea.selectionStart;
            const endPos = textarea.selectionEnd;
            const originalVal = textarea.value;
            
            textarea.value = originalVal.substring(0, startPos) + code + originalVal.substring(endPos);
            textarea.dispatchEvent(new Event('input'));
            textarea.focus();
            
            const newCursorPos = startPos + code.length;
            textarea.setSelectionRange(newCursorPos, newCursorPos);
        }

        // Toggle thinking style micro-interactions
        function toggleThinkingStyle() {
            const toggle = document.getElementById('aiThinkingToggle');
            const icon = document.getElementById('thinkingIcon');
            const label = document.getElementById('thinkingLabel');
            if (toggle.checked) {
                icon.className = "fa-solid fa-brain text-brand-500 animate-pulse";
                label.textContent = "深度思考";
            } else {
                icon.className = "fa-solid fa-bolt text-amber-500";
                label.textContent = "极速解答";
            }
        }

        // 3. AI Intelligent Solve handler
        function triggerAISolve() {
            const content = document.getElementById('editContent').value;
            const qtype = document.getElementById('editQType').value;
            const customPrompt = document.getElementById('aiCustomPrompt').value;
            
            // Use globally configured preferred solve model
            const model = typeof systemPreferSolveModel !== 'undefined' ? systemPreferSolveModel : 'deepseek-v4-pro';
            const thinkingToggle = document.getElementById('aiThinkingToggle');
            const thinking = (thinkingToggle && thinkingToggle.checked) ? 'enabled' : 'disabled';
            
            if (!content.trim()) {
                showToast('请先在上方输入题干内容，AI需要读取题干生成解答步骤！', 'error');
                return;
            }
            
            const btn = document.getElementById('aiSolveBtn');
            const loader = document.getElementById('aiLoadingIndicator');
            const outputBox = document.getElementById('aiOutputBox');
            const resultBox = document.getElementById('aiResultText');
            const loadingText = document.getElementById('aiLoadingText');
            
            const ocrResultTextEl = document.getElementById('ocrResultText');
            const ocrResult = ocrResultTextEl ? ocrResultTextEl.textContent.trim() : '';

            // Set dynamic loading explanation depending on thinking mode and model
            let modelFriendly = model.includes('/') ? model.split('/').pop() : model;

            if (ocrResult) {
                if (thinking === 'enabled') {
                    loadingText.textContent = `${modelFriendly} 正在结合题干与 OCR 结果进行深度思考并构建 LaTeX 解析... (大约需要 15-90 秒)`;
                } else {
                    loadingText.textContent = `${modelFriendly} 正在结合题干与 OCR 结果极速生成 LaTeX 解析... (预计 3-10 秒)`;
                }
            } else {
                if (thinking === 'enabled') {
                    loadingText.textContent = `${modelFriendly} 正在进行深度思考并构建 LaTeX 解析步骤... (思考与生成可能需要 15-90 秒，请耐心等待)`;
                } else {
                    loadingText.textContent = `${modelFriendly} 正在极速生成简要 LaTeX 解析步骤... (预计 3-10 秒即可完成，请稍后)`;
                }
            }
            
            btn.disabled = true;
            btn.classList.add('opacity-50', 'pointer-events-none');
            loader.classList.remove('hidden');
            outputBox.classList.add('hidden');
            
            // Initialize progress bar
            const progressBar = document.getElementById('aiSolveProgressBar');
            if (progressBar) {
                progressBar.style.width = '0%';
            }
            
            const formData = new FormData();
            formData.append('content', content);
            formData.append('question_type', qtype);
            formData.append('ocr_result', ocrResult);
            formData.append('custom_prompt', customPrompt);
            formData.append('thinking', thinking);
            formData.append('model', model);
            formData.append('stream', 'true'); // Opt-in to real-time streaming
            
            abortActiveAiSolve();
            const editorSnapshot = EditorState.snapshot();
            const requestSequence = ++aiSolveRequestSequence;
            const requestController = new AbortController();
            aiSolveAbortController = requestController;

            const requestIsCurrent = () => isAiSolveRequestCurrent(
                requestSequence,
                requestController,
                editorSnapshot
            );

            const finishCurrentRequest = (resetProgress = false) => {
                if (!requestIsCurrent()) return false;
                aiSolveAbortController = null;
                if (aiSolveCompletionTimer) {
                    clearTimeout(aiSolveCompletionTimer);
                    aiSolveCompletionTimer = null;
                }
                resetAiSolveUi(resetProgress);
                return true;
            };
            
            fetch('/api/ai/solve', {
                method: 'POST',
                body: formData,
                signal: requestController.signal
            })
            .then(response => {
                if (!requestIsCurrent()) return;
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}`);
                }
                if (progressBar) progressBar.style.width = '3%';
                
                const reader = response.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let buffer = '';
                let accumulatedSolution = '';
                let accumulatedReasoning = '';
                
                function read() {
                    return reader.read().then(({ done, value }) => {
                        if (!requestIsCurrent()) return;
                        if (done) {
                            throw new Error('AI 流式响应意外中断');
                        }
                        
                        buffer += decoder.decode(value, { stream: true });
                        const lines = buffer.split('\n');
                        buffer = lines.pop(); // Keep last incomplete line
                        
                        for (const line of lines) {
                            const trimmed = line.trim();
                            if (trimmed.startsWith('data:')) {
                                try {
                                    const eventData = JSON.parse(trimmed.slice(5).trim());
                                    if (!requestIsCurrent()) return;
                                    if (eventData.status === 'processing') {
                                        if (eventData.reasoning) {
                                            accumulatedReasoning += eventData.reasoning;
                                            const rCount = eventData.reasoning_count || 0;
                                            const rPct = Math.min(50, rCount * 0.08); // Up to 50%
                                            if (progressBar) progressBar.style.width = `${3 + rPct}%`;
                                            if (loadingText) {
                                                loadingText.textContent = `${modelFriendly} 正在进行深度推理思考 (已生成 ${rCount} 个 Token)...`;
                                            }
                                        }
                                        if (eventData.content) {
                                            accumulatedSolution += eventData.content;
                                            const cCount = eventData.content_count || 0;
                                            const rCount = eventData.reasoning_count || 0;
                                            let pct = 3;
                                            if (rCount > 0) {
                                                const rPct = Math.min(50, rCount * 0.08);
                                                const cPct = Math.min(45, cCount * 0.05);
                                                pct += rPct + cPct;
                                            } else {
                                                pct += Math.min(92, cCount * 0.08);
                                            }
                                            if (progressBar) progressBar.style.width = `${pct}%`;
                                            if (loadingText) {
                                                loadingText.textContent = `${modelFriendly} 正在生成 LaTeX 解析步骤 (已输出 ${cCount} 个 Token)...`;
                                            }
                                        }
                                    } else if (eventData.status === 'error') {
                                        return Promise.reject(new Error(eventData.message || 'AI 服务返回错误'));
                                    } else if (eventData.status === 'done') {
                                        if (progressBar) progressBar.style.width = '100%';
                                        aiSolveCompletionTimer = setTimeout(() => {
                                            aiSolveCompletionTimer = null;
                                            // The editor can change during the 300ms
                                            // completion animation. Recheck before
                                            // touching either result field.
                                            if (!requestIsCurrent()) return;
                                            outputBox.classList.remove('hidden');
                                            
                                            let finalOutput = '';
                                            if (accumulatedReasoning.trim()) {
                                                finalOutput += `【深度思考推理过程】\n${accumulatedReasoning.trim()}\n\n【参考解析】\n`;
                                            }
                                            finalOutput += accumulatedSolution;
                                            resultBox.textContent = finalOutput;
                                            
                                            loadToFinalReview('ai');
                                            showToast('AI 解析生成成功！');
                                            finishCurrentRequest();
                                        }, 300);
                                        return;
                                    }
                                } catch (e) {
                                    console.error('Failed to parse SSE line:', line, e);
                                }
                            }
                        }
                        return read();
                    });
                }
                
                return read();
            })
            .catch(err => {
                // A superseded request must not clear or re-enable the controls
                // owned by the new request.
                if (!requestIsCurrent()) return;
                finishCurrentRequest(true);
                if (err.name !== 'AbortError') {
                    showToast('AI 生成解析出错: ' + err.message, 'error');
                }
            });
        }

        // Import Tab results into persistent Final Review Textbox
        function loadToFinalReview(source) {
            const finalEdit = document.getElementById('editAnswerMarkdown');
            let contentToImport = '';
            
            if (source === 'ai') {
                contentToImport = document.getElementById('aiResultText').textContent;
                // Exclude reasoning block from importing into the final review editor
                if (contentToImport.includes('【参考解析】')) {
                    const parts = contentToImport.split('【参考解析】');
                    contentToImport = parts[1] || parts[0];
                }
            } else if (source === 'ocr') {
                contentToImport = document.getElementById('ocrResultText').textContent;
            }
            
            // Clean up LaTeX spacing, formula noise and leading question numbers
            if (source === 'ai' || source === 'ocr') {
                contentToImport = cleanMathOcrText(contentToImport);
            }
            
            if (!contentToImport.trim()) {
                showToast('导入内容为空！', 'error');
                return;
            }
            
            // Ask user whether to replace or append if there is already content
            if (finalEdit.value.trim()) {
                const replace = confirm('终审编辑框中已有内容，点击"确定"将替换已有内容，点击"取消"将追加在后面。');
                if (replace) {
                    finalEdit.value = contentToImport;
                } else {
                    finalEdit.value += '\n\n' + contentToImport;
                }
            } else {
                finalEdit.value = contentToImport;
            }
            
            // Refresh preview
            finalEdit.dispatchEvent(new Event('input'));
            showToast('已成功载入至终审编辑框！');
        }

        // Clear Draft
