$(document).ready(function() {
    $.summernote.lang['ko-KR'].color.cpSelect = 'color picker';

    const MAX_CHARS = 5000;

    $('#summernote-editor').summernote({
        lang: 'ko-KR', // 한국어 설정
        height: '100%', // 부모 요소에 맞게 높이 조절
        minHeight: 500, // 최소 높이
        maxHeight: 500, // 최대 높이
        focus: true, // 에디터 로딩 후 포커스
        placeholder: '내용을 입력해주세요.',
        toolbar: [
            ['font', ['bold', 'underline']],
            ['color', ['forecolor', 'backcolor']],
            ['para', ['paragraph']],
            ['table', ['table']],
            ['insert', ['picture', 'video']]
        ],
        callbacks: {
            onKeyup: function(e) {
                updateCharCount(this);
            },
            onPaste: function(e) {
                // 붙여넣기 후에도 카운트를 업데이트하기 위해 약간의 지연을 줌
                setTimeout(() => {
                    updateCharCount(this);
                }, 10);
            },
            onChange: function(contents, $editable) {
                // 내용이 변경될 때마다 호출 (삭제, 붙여넣기 등 포함)
                updateCharCount(this);
            }
        }
    });
    
    // 초기 글자 수 설정
    $('#max-chars').text(MAX_CHARS.toLocaleString());

    function updateCharCount(editorInstance) {
        // Summernote의 code() 메서드는 HTML 태그를 포함하므로,
        // 순수 텍스트 길이를 계산하기 위해 태그를 제거합니다.
        const content = $(editorInstance).summernote('code');
        const text = $('<div>').html(content).text();
        const currentLength = text.length;

        const counterElement = $('#current-chars');
        const counterWrapper = $('.char-counter');
        
        counterElement.text(currentLength.toLocaleString());

        if (currentLength > MAX_CHARS) {
            counterWrapper.addClass('limit-exceeded');
        } else {
            counterWrapper.removeClass('limit-exceeded');
        }
    }
});