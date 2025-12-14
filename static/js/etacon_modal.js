document.addEventListener('DOMContentLoaded', function() {
    // 1. 모달 HTML 생성 (한 번만 생성됨)
    if (!document.querySelector('.etacon-modal-overlay')) {
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
    }

    const modalOverlay = document.querySelector('.etacon-modal-overlay');
    const closeBtn = modalOverlay.querySelector('.etacon-modal-close');
    const tabsContainer = document.getElementById('etacon-tabs');
    const gridsContainer = document.getElementById('etacon-grids');
    
    let etaconsLoaded = false;
    
    // 전송 시 필요한 데이터 저장 변수
    let currentPostId = null;
    let currentParentId = null;

    // --- 1. 모달 열기 함수 ---
    // 비회원은 사용 불가하므로 formId 파라미터는 필요 없습니다.
    window.openEtaconModal = function(postId, parentId = null) {
        currentPostId = postId;
        currentParentId = parentId;
        
        modalOverlay.style.display = 'flex';
        
        if (!etaconsLoaded) {
            fetchEtacons();
        }
    };

    // 닫기 이벤트
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
            if (response.status === 401 || response.status === 403) {
                gridsContainer.innerHTML = '<div class="etacon-message">로그인이 필요합니다.</div>';
                return;
            }
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
            
            tab.addEventListener('click', () => switchTab(index));
            tabsContainer.appendChild(tab);

            // 그리드 생성
            const grid = document.createElement('div');
            grid.className = `etacon-grid ${index === 0 ? 'active' : ''}`;
            
            data[packName].forEach(etacon => {
                const item = document.createElement('div');
                item.className = 'etacon-item';
                
                const img = document.createElement('img');
                img.src = `/static/${etacon.image_path}`; // 경로 주의
                img.alt = 'etacon';
                
                item.appendChild(img);
                // 클릭 시 전송 함수 호출
                item.addEventListener('click', () => sendEtacon(etacon.code));
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

    // --- 4. 에타콘 전송 로직 (API 호출) ---
    async function sendEtacon(code) {
        // 전송할 데이터 (비회원 정보 불필요)
        const payload = {
            post_id: currentPostId,
            parent_comment_id: currentParentId,
            etacon_code: code
        };

        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
            
            const response = await fetch('/api/comment/etacon', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                },
                body: JSON.stringify(payload)
            });

            const result = await response.json();

            if (result.status === 'success') {
                window.location.reload(); // 성공 시 새로고침
            } else {
                alert(result.message);
                modalOverlay.style.display = 'none'; // 실패 시 모달 닫기
            }
        } catch (error) {
            console.error('Error:', error);
            alert('에타콘 전송 중 오류가 발생했습니다.');
        }
    }
});