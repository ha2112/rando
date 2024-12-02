import numpy as np
import cv2
import tensorflow as tf
from tensorflow.keras import layers, models

# Create a CNN model for digit recognition
def create_model():
    model = models.Sequential([
        layers.Conv2D(32, (3, 3), activation='relu', input_shape=(28, 28, 1)),
        layers.MaxPooling2D((2, 2)),
        layers.Conv2D(64, (3, 3), activation='relu'),
        layers.MaxPooling2D((2, 2)),
        layers.Conv2D(64, (3, 3), activation='relu'),
        layers.Flatten(),
        layers.Dense(64, activation='relu'),
        layers.Dense(10, activation='softmax')
    ])
    model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])
    return model

# Load and preprocess image for prediction
def preprocess_image(image_path):
    # Load image and convert to grayscale
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    # Resize to 28x28 (MNIST format)
    img = cv2.resize(img, (28, 28))
    # Normalize pixel values
    img = img.astype('float32') / 255.0
    # Reshape for model input
    img = np.expand_dims(img, axis=0)
    img = np.expand_dims(img, axis=-1)
    return img

# Train the model
def train_model():
    # Load MNIST dataset
    (x_train, y_train), (x_test, y_test) = tf.keras.datasets.mnist.load_data()
    
    # Preprocess training data
    x_train = x_train.astype('float32') / 255.0
    x_test = x_test.astype('float32') / 255.0
    x_train = np.expand_dims(x_train, axis=-1)
    x_test = np.expand_dims(x_test, axis=-1)
    
    # Create and train model
    model = create_model()
    model.fit(x_train, y_train, epochs=5, validation_data=(x_test, y_test))
    
    # Save the trained model
    model.save('digit_model.h5')
    return model

# Predict digit from image
def predict_digit(image_path, model=None):
    if model is None:
        try:
            model = tf.keras.models.load_model('my_model.h5')
        except:
            print("No saved model found. Training new model...")
            model = train_model()
    
    # Preprocess image and predict
    img = preprocess_image(image_path)
    prediction = model.predict(img)
    return np.argmax(prediction[0])

#############################################################
# if __name__ == "__main__":
#     # Example usage
#     model = train_model()  # Train model first time
    
#     # Test prediction on sample image
#     # Replace 'test_digit.png' with your image path
#     try:
#         digit = predict_digit('test_digit.png', model)
#         print(f"Predicted digit: {digit}")
#     except Exception as e:
#         print(f"Error predicting digit: {str(e)}")

##############################################################
model = keras.models.load_model("my_model.h5")
model.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])
path = "eight.png"
img = preprocess_image(path)
prediction = model.predict(img)
print(prediction)

