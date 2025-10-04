document.addEventListener('DOMContentLoaded', () => {
    // --- ELEMENT SELECTION ---
    const card = document.getElementById('flashcard');
    const questionEl = document.getElementById('card-question');
    const answerEl = document.getElementById('card-answer');
    const progressText = document.getElementById('progress-text');
    const flipButton = document.getElementById('flip-button');
    const prevButton = document.getElementById('prev-button');
    const nextButton = document.getElementById('next-button');
    const mobilePrevButton = document.getElementById('mobile-prev-button');
    const mobileNextButton = document.getElementById('mobile-next-button');

    let flashcards = [];
    let currentIndex = 0;

    // --- DATA FETCHING ---
    fetch('flashcard_data.json')
        .then(response => response.json())
        .then(data => {
            flashcards = data.sort(() => Math.random() - 0.5); // Shuffle deck on load
            if (flashcards.length > 0) {
                displayCard();
            } else {
                questionEl.textContent = "No flashcards found.";
            }
        })
        .catch(error => {
            console.error("Error loading flashcards:", error);
            questionEl.innerHTML = "Error: Could not load 'flashcard_data.json'.";
        });

    // --- CORE FUNCTIONS ---
    function displayCard() {
        if (flashcards.length === 0) {
            questionEl.innerHTML = "<h2>🎉 Session Complete! 🎉</h2>";
            answerEl.innerHTML = "";
            progressText.textContent = "All cards reviewed.";
            document.querySelector('.navigation').style.display = 'none';
            mobilePrevButton.style.display = 'none';
            mobileNextButton.style.display = 'none';
            return;
        }

        const currentCard = flashcards[currentIndex];
        questionEl.innerHTML = `<strong>Q:</strong> ${currentCard.question}`;

        let noteContent = currentCard.note || ""; // Handle empty notes
        let answerHTML = `<strong>A:</strong> ${marked.parse(noteContent)}`;

        if (currentCard.code && currentCard.code.trim() !== "") {
            const escapedCode = currentCard.code.replace(/</g, "&lt;").replace(/>/g, "&gt;");
            answerHTML += `<h3>Code:</h3><pre><code class="python">${escapedCode}</code></pre>`;
        }
        if (currentCard.image && currentCard.image.trim() !== "") {
            answerHTML += `<h3>Output:</h3><img src="images/${currentCard.image}" alt="Output Image">`;
        }

        answerEl.innerHTML = answerHTML;

        try {
            hljs.highlightAll();
        } catch (e) { console.error("highlight.js failed to run", e); }

        progressText.textContent = `Card ${currentIndex + 1} of ${flashcards.length}`;
        card.classList.remove('is-flipped');
    }

    function flipCard() {
        card.classList.toggle('is-flipped');
    }

    // --- NAVIGATION LOGIC ---
    function nextCard() {
        const isFlipped = card.classList.contains('is-flipped');

        const loadNext = () => {
            currentIndex = (currentIndex + 1) % flashcards.length;
            displayCard();
        };

        if (isFlipped) {
            card.addEventListener('transitionend', loadNext, { once: true });
            card.classList.remove('is-flipped');
        } else {
            loadNext();
        }
    }

    function prevCard() {
        const isFlipped = card.classList.contains('is-flipped');

        const loadPrev = () => {
            currentIndex = (currentIndex - 1 + flashcards.length) % flashcards.length;
            displayCard();
        };

        if (isFlipped) {
            card.addEventListener('transitionend', loadPrev, { once: true });
            card.classList.remove('is-flipped');
        } else {
            loadPrev();
        }
    }

    // --- EVENT LISTENERS ---
    flipButton.addEventListener('click', flipCard);
    card.addEventListener('click', flipCard);
    nextButton.addEventListener('click', nextCard);
    prevButton.addEventListener('click', prevCard);

    mobilePrevButton.addEventListener('click', (e) => {
        e.stopPropagation();
        prevCard();
    });
    mobileNextButton.addEventListener('click', (e) => {
        e.stopPropagation();
        nextCard();
    });

    document.addEventListener('keydown', (e) => {
        if (flashcards.length === 0) return;
        if (e.code === 'Space') {
            e.preventDefault();
            flipCard();
        } else if (e.code === 'ArrowRight') {
            nextCard();
        } else if (e.code === 'ArrowLeft') {
            prevCard();
        }
    });
});
