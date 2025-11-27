// static/js/etacon_modal.js

document.addEventListener('DOMContentLoaded', function() {
    const modalOverlay = document.createElement('div');
    modalOverlay.className = 'etacon-modal-overlay';
    modalOverlay.innerHTML = `
        <div class="etacon-modal">
            <div class="etacon-modal-header">
                <h3 class="etacon-modal-title">보유 에타콘</h3>
                <button class="etacon-modal-close">&times;</button>
            </div>
            <div class="etacon-tabs" id="etacon-tabs"></div>
            <div class="etacon-grid-container" id="etacon-grids">
                <div class="etacon-message">로딩 중...</div>
            </div>
        </div>
    `;
    document.body.appendChild(modalOverlay);

    const closeBtn = modalOverlay.querySelector('.etacon-modal-close');
    const tabsContainer = document.getElementById('etacon-tabs');
    const gridsContainer = document.getElementById('etacon-grids');
    
    let etaconsLoaded = false;
    let targetInput = null; // 에타콘을 삽입할 대상 (summernote 또는 textarea)

    // --- 1. 모달 열기/닫기 함수 ---
    window.openEtaconModal = function(targetSelector) {
        targetInput = targetSelector; // '#summernote-editor' 또는 'textarea[name="comment_content"]'
        modalOverlay.style.display = 'flex';
        if (!etaconsLoaded) {
            fetchEtacons();
        }
    };

    closeBtn.addEventListener('click', () => {
        modalOverlay.style.display = 'none';
    });

    modalOverlay.addEventListener('click', (e) => {
        if (e.target === modalOverlay) {
            modalOverlay.style.display = 'none';
        }
    });

    // --- 2. 에타콘 목록 불러오기 ---
    async function fetchEtacons() {
        try {
            const response = await fetch('/api/my-etacons');
            const data = await response.json();
            
            renderEtacons(data);
            etaconsLoaded = true;
        } catch (error) {
            gridsContainer.innerHTML = '<div class="etacon-message">에타콘을 불러오는데 실패했습니다.</div>';
            console.error(error);
        }
    }

    // --- 3. 렌더링 (탭 & 그리드) ---
    function renderEtacons(data) {
        tabsContainer.innerHTML = '';
        gridsContainer.innerHTML = '';

        const packNames = Object.keys(data);

        if (packNames.length === 0) {
            gridsContainer.innerHTML = '<div class="etacon-message">보유한 에타콘이 없습니다.<br><a href="/etacon/shop" style="color:#E53935;">상점 바로가기</a></div>';
            return;
        }

        packNames.forEach((packName, index) => {
            // 탭 생성
            const tab = document.createElement('div');
            tab.className = `etacon-tab ${index === 0 ? 'active' : ''}`;
            tab.textContent = packName;
            tab.dataset.target = `pack-${index}`;
            
            tab.addEventListener('click', () => switchTab(index));
            tabsContainer.appendChild(tab);

            // 그리드 생성
            const grid = document.createElement('div');
            grid.className = `etacon-grid ${index === 0 ? 'active' : ''}`;
            grid.id = `pack-${index}`;

            data[packName].forEach(etacon => {
                const item = document.createElement('div');
                item.className = 'etacon-item';
                
                const img = document.createElement('img');
                img.src = `/static/${etacon.image_path}`;
                img.alt = 'etacon';
                // 데이터 속성에 코드 저장 (~pack_idx)
                img.dataset.code = etacon.code; 
                
                item.appendChild(img);
                item.addEventListener('click', () => insertEtacon(etacon));
                grid.appendChild(item);
            });

            gridsContainer.appendChild(grid);
        });
    }

    function switchTab(activeIndex) {
        const tabs = document.querySelectorAll('.etacon-tab');
        const grids = document.querySelectorAll('.etacon-grid');

        tabs.forEach((tab, idx) => {
            if (idx === activeIndex) tab.classList.add('active');
            else tab.classList.remove('active');
        });

        grids.forEach((grid, idx) => {
            if (idx === activeIndex) grid.classList.add('active');
            else grid.classList.remove('active');
        });
    }

    // --- 4. 에타콘 삽입 로직 (핵심) ---
    function insertEtacon(etacon) {
        const imgUrl = `/static/${etacon.image_path}`;
        const code = etacon.code; // 예: ~15_0

        if (targetInput === 'summernote') {
            // [게시글 작성] Summernote에 실제 이미지 태그 삽입
            // data-code 속성을 심어서 나중에 전송 시 텍스트로 변환할 수 있게 함
            const imgNode = document.createElement('img');
            imgNode.src = imgUrl;
            imgNode.className = 'etacon-img-preview'; // 식별용 클래스
            imgNode.dataset.code = code;
            imgNode.style.maxWidth = '100px';
            
            $('#summernote-editor').summernote('insertNode', imgNode);
            
        } else {
            // [댓글 작성] 일반 Textarea에는 텍스트 코드 삽입
            const textarea = document.querySelector(targetInput);
            if (textarea) {
                const start = textarea.selectionStart;
                const end = textarea.selectionEnd;
                const text = textarea.value;
                const before = text.substring(0, start);
                const after = text.substring(end, text.length);
                
                textarea.value = before + code + after;
                textarea.focus();
                textarea.selectionEnd = start + code.length;
            }
        }
        
        modalOverlay.style.display = 'none';
    }

    // --- 5. 폼 제출 가로채기 (이미지 -> 텍스트 코드 변환) ---
    // 게시글 작성 폼 (#post-form) 제출 시 실행
    const postForm = document.getElementById('post-form');
    if (postForm) {
        postForm.addEventListener('submit', function(e) {
            // Summernote 내용을 가져옴
            const content = $('#summernote-editor').summernote('code');
            
            // 임시 DOM을 만들어 조작
            const tempDiv = document.createElement('div');
            tempDiv.innerHTML = content;

            // 1. 방금 삽입한 미리보기 이미지 (.etacon-img-preview)
            // 2. 서버에서 불러온 기존 이미지 (.etacon-img) 
            // 모두 찾아서 텍스트 코드로 변환
            const images = tempDiv.querySelectorAll('img.etacon-img-preview, img.etacon-img');
            
            images.forEach(img => {
                // data-code 속성이 있거나, ~로 시작하는 코드를 찾을 수 있다면 변환
                const code = img.dataset.code; 
                if (code) {
                    const textNode = document.createTextNode(code);
                    img.parentNode.replaceChild(textNode, img);
                }
            });

            // 변환된 텍스트 내용을 hidden input에 저장
            const contentInput = document.getElementById('post-content');
            if (contentInput) {
                contentInput.value = tempDiv.innerHTML;
            }
        });
    }
});