document.addEventListener('DOMContentLoaded', () => {
    const card = document.getElementById('flashcard');
    const questionEl = document.getElementById('card-question');
    const answerEl = document.getElementById('card-answer');
    const progressText = document.getElementById('progress-text');
    const flipButton = document.getElementById('flip-button');
    const prevButton = document.getElementById('prev-button');
    const nextButton = document.getElementById('next-button');

    let flashcards = [];
    let currentIndex = 0;

    // Load the flashcard data from the JSON file
    fetch('flashcard_data.json')
        .then(response => response.json())
        .then(data => {
            flashcards = data;
            if (flashcards.length > 0) {
                displayCard();
            } else {
                questionEl.innerHTML = "No flashcards found. Please run the Python script.";
            }
        })
        .catch(error => {
            console.error("Error loading flashcards:", error);
            questionEl.innerHTML = "Error: Could not load 'flashcard_data.json'. Make sure the file exists.";
        });

    function displayCard() {
        if (flashcards.length === 0) return;

        const currentCard = flashcards[currentIndex];

        // Display question and note
        questionEl.innerHTML = `<strong>Q:</strong> ${currentCard.question}<br><br><small>(${currentCard.chapter} - ${currentCard.marks} Marks)</small>`;
        answerEl.innerHTML = currentCard.note.replace(/\n/g, '<br>'); // Respect line breaks

        // Update progress
        progressText.textContent = `Card ${currentIndex + 1} of ${flashcards.length}`;

        // Reset flip state
        card.classList.remove('is-flipped');
    }

    function flipCard() {
        card.classList.toggle('is-flipped');
    }

    function nextCard() {
        currentIndex = (currentIndex + 1) % flashcards.length; // Loop back to the start
        displayCard();
    }

    function prevCard() {
        currentIndex = (currentIndex - 1 + flashcards.length) % flashcards.length; // Loop back to the end
        displayCard();
    }

    // Event Listeners
    flipButton.addEventListener('click', flipCard);
    card.addEventListener('click', flipCard);
    nextButton.addEventListener('click', nextCard);
    prevButton.addEventListener('click', prevCard);

    // Keyboard navigation
    document.addEventListener('keydown', (e) => {
        if (e.code === 'Space') {
            e.preventDefault(); // Prevent page scrolling
            flipCard();
        } else if (e.code === 'ArrowRight') {
            nextCard();
        } else if (e.code === 'ArrowLeft') {
            prevCard();
        }
    });
});
