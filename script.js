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
    const filterToggle = document.getElementById('filter-toggle');
    const filterOptions = document.getElementById('filter-options');
    const chapterFilter = document.getElementById('chapter-filter');
    const marksFilter = document.getElementById('marks-filter');

    // --- STATE VARIABLES ---
    let allFlashcards = [];
    let filteredDeck = [];
    let currentIndex = 0;

    // Configure Marked with KaTeX
    marked.use(markedKatex({
        throwOnError: false,
        output: 'html'
    }));

    // --- DATA FETCHING ---
    fetch('flashcard_data.json')
        .then(response => response.json())
        .then(data => {
            allFlashcards = data;
            if (allFlashcards.length > 0) {
                populateFilters();
                applyFilters();
            } else {
                questionEl.textContent = "No flashcards found.";
            }
        })
        .catch(error => {
            console.error("Error loading flashcards:", error);
            questionEl.innerHTML = "Error: Could not load 'flashcard_data.json'.";
        });

    // --- FILTERING LOGIC ---
    function populateFilters() {
        const chapters = [...new Set(allFlashcards.map(c => c.chapter))].sort();
        const marks = [...new Set(allFlashcards.map(c => c.marks))].sort((a, b) => a - b);

        chapters.forEach(chapter => {
            const option = document.createElement('option');
            option.value = chapter;
            option.textContent = chapter;
            chapterFilter.appendChild(option);
        });

        marks.forEach(mark => {
            const option = document.createElement('option');
            option.value = mark;
            option.textContent = `${mark} Marks`;
            marksFilter.appendChild(option);
        });
    }

    function applyFilters() {
        const selectedChapter = chapterFilter.value;
        const selectedMarks = marksFilter.value;
        let tempDeck = [...allFlashcards];

        if (selectedChapter !== 'all') {
            tempDeck = tempDeck.filter(card => card.chapter === selectedChapter);
        }
        if (selectedMarks !== 'all') {
            tempDeck = tempDeck.filter(card => card.marks === parseInt(selectedMarks, 10));
        }

        filteredDeck = tempDeck;
        currentIndex = 0;
        displayCard();
    }

    // --- CORE FUNCTIONS ---
    function displayCard() {
        if (filteredDeck.length === 0) {
            questionEl.innerHTML = "<h2>No cards match your filter.</h2>";
            answerEl.innerHTML = "";
            progressText.textContent = "Card 0 of 0";
            return;
        }

        // --- 1. FIX: INSTANT RESET (Prevent "Quick Flip" Glitch) ---
        // Instantly snap card to front without animation before changing content
        card.style.transition = 'none';
        card.classList.remove('is-flipped');

        // Force browser to accept the change immediately
        void card.offsetWidth;

        // Restore animation so the user can flip it manually later
        setTimeout(() => {
            card.style.transition = '';
        }, 50);

        // --- 2. RENDER CONTENT ---
        const currentCard = filteredDeck[currentIndex];
        questionEl.innerHTML = `<strong>Q:</strong> ${currentCard.question} (${currentCard.marks}m)`;

        let answerHTML = `<strong>A:</strong> ${marked.parse(currentCard.note || "")}`;

        // --- 3. COMPATIBILITY & LANGUAGE LOGIC ---
        if (currentCard.code && currentCard.code.trim() !== "") {
            const escapedCode = currentCard.code.replace(/</g, "&lt;").replace(/>/g, "&gt;");

            // Logic: If 'language' exists in JSON, use it. If not, default to 'python'.
            const langClass = currentCard.language || 'python';

            answerHTML += `<h3>Code:</h3><pre><code class="language-${langClass}">${escapedCode}</code></pre>`;
        }

        if (currentCard.image && typeof currentCard.image === 'string' && currentCard.image.trim() !== "") {
            answerHTML += `<h3>Output:</h3><img src="images/${currentCard.image}" alt="Output Image">`;
        }

        answerEl.innerHTML = answerHTML;

        // Apply Syntax Highlighting to the new content
        try { hljs.highlightAll(); } catch (e) { }

        progressText.textContent = `Card ${currentIndex + 1} of ${filteredDeck.length}`;
    }

    function flipCard() {
        card.classList.toggle('is-flipped');
    }

    function nextCard() {
        if (filteredDeck.length === 0) return;
        currentIndex = (currentIndex + 1) % filteredDeck.length;
        displayCard();
    }

    function prevCard() {
        if (filteredDeck.length === 0) return;
        currentIndex = (currentIndex - 1 + filteredDeck.length) % filteredDeck.length;
        displayCard();
    }

    // --- EVENT LISTENERS ---
    flipButton.addEventListener('click', flipCard);

    // Clicking card flips it, unless selecting text in code block
    card.addEventListener('click', (e) => {
        if (e.target.closest('pre') || e.target.closest('code')) return;
        flipCard();
    });

    nextButton.addEventListener('click', nextCard);
    prevButton.addEventListener('click', prevCard);

    mobilePrevButton.addEventListener('click', (e) => { e.stopPropagation(); prevCard(); });
    mobileNextButton.addEventListener('click', (e) => { e.stopPropagation(); nextCard(); });

    chapterFilter.addEventListener('change', applyFilters);
    marksFilter.addEventListener('change', applyFilters);
    filterToggle.addEventListener('click', () => {
        filterOptions.classList.toggle('is-open');
    });

    document.addEventListener('keydown', (e) => {
        if (filteredDeck.length === 0) return;
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
