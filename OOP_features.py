import os
import requests
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import torch
from torchvision import models, transforms
from PIL import Image
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import normalize
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.neighbors import NearestNeighbors

# Create a directory to save the downloaded images
image_dir = "/mnt/data/product_images"
os.makedirs(image_dir, exist_ok=True)

# Function to download a single image
def download_image(idx, url, save_dir):
    try:
        response = requests.get(url, stream=True, timeout=10)
        if response.status_code == 200:
            with open(os.path.join(save_dir, f"image_{idx}.jpg"), "wb") as file:
                for chunk in response.iter_content(1024):
                    file.write(chunk)
        return True
    except Exception as e:
        return False

# Function to download images with multithreading
def download_images_multithreaded(image_links, save_dir, max_threads=10):
    with ThreadPoolExecutor(max_threads) as executor:
        results = list(tqdm(
            executor.map(lambda args: download_image(*args), enumerate(image_links)),
            total=len(image_links),
            desc="Downloading images"
        ))
    failed_count = results.count(False)
    print(f"Download complete. {failed_count} images failed.")

# Filter valid image links
valid_image_links = [url for url in dataset['image_links'].dropna().unique() if url.startswith("http")]

# Start downloading images
download_images_multithreaded(valid_image_links, image_dir)

# Step 2: Extract Image Embeddings using ResNet
# Load a pre-trained ResNet model
model = models.resnet50(pretrained=True)
model = torch.nn.Sequential(*(list(model.children())[:-1]))  # Remove the classification layer
model.eval()

# Define a transformation pipeline for images
transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

# Function to extract embeddings from an image
def extract_embedding(image_path):
    try:
        image = Image.open(image_path).convert("RGB")
        image = transform(image).unsqueeze(0)  # Add batch dimension
        with torch.no_grad():
            embedding = model(image).squeeze().numpy()
        return embedding
    except Exception as e:
        print(f"Error processing {image_path}: {e}")
        return None

# Process all images and extract embeddings
embeddings = {}
image_files = [os.path.join(image_dir, f) for f in os.listdir(image_dir) if f.endswith(".jpg")]

for image_file in tqdm(image_files, desc="Extracting embeddings"):
    embedding = extract_embedding(image_file)
    if embedding is not None:
        embeddings[os.path.basename(image_file)] = embedding

# Save embeddings to a file for future use
np.save("/mnt/data/image_embeddings.npy", embeddings)
print("Embeddings extracted and saved.")

# Step 3: Similarity Matching Logic
# Load embeddings
embeddings = np.load("/mnt/data/image_embeddings.npy", allow_pickle=True).item()

def find_similar_images(query_image_path, embeddings, top_k=5):
    # Extract the embedding for the query image
    query_embedding = extract_embedding(query_image_path)
    if query_embedding is None:
        return []

    # Compute cosine similarity between the query and all other images
    image_names = list(embeddings.keys())
    image_embeddings = np.array(list(embeddings.values()))
    similarities = cosine_similarity([query_embedding], image_embeddings)[0]

    # Get the top_k most similar images
    top_indices = similarities.argsort()[-top_k:][::-1]
    return [(image_names[i], similarities[i]) for i in top_indices]

# Example usage
query_image_path = "/mnt/data/product_images/image_0.jpg"  # Replace with the actual query image path
similar_images = find_similar_images(query_image_path, embeddings)
print("Top similar images:")
for img_name, score in similar_images:
    print(f"{img_name}: {score}")

# Step 4: Recommendation System
# Load user-item interaction data (sample dataset)
data = {
    'user_id': [1, 1, 1, 2, 2, 3, 3, 3, 3],
    'item_id': [101, 102, 103, 101, 104, 102, 103, 104, 105],
    'interaction': [1, 1, 1, 1, 1, 1, 1, 1, 1]
}
user_item_df = pd.DataFrame(data)

# Create a user-item interaction matrix
interaction_matrix = user_item_df.pivot_table(index='user_id', columns='item_id', values='interaction', fill_value=0)
interaction_sparse = csr_matrix(interaction_matrix.values)

# Build a collaborative filtering model using k-NN
knn = NearestNeighbors(metric='cosine', algorithm='brute')
knn.fit(interaction_sparse)

# Function to recommend items based on a user

def recommend_items(user_id, interaction_matrix, knn_model, top_k=5):
    if user_id not in interaction_matrix.index:
        return []

    user_index = interaction_matrix.index.get_loc(user_id)
    distances, indices = knn_model.kneighbors(interaction_sparse[user_index], n_neighbors=top_k+1)

    recommendations = []
    for i in range(1, len(distances.flatten())):  # Skip the first neighbor (self)
        similar_user_index = indices.flatten()[i]
        similar_user_id = interaction_matrix.index[similar_user_index]
        similar_user_items = interaction_matrix.loc[similar_user_id]
        recommended_items = similar_user_items[similar_user_items > 0].index.tolist()
        recommendations.extend(recommended_items)

    return list(set(recommendations))  # Remove duplicates

# Example usage
user_id = 1
recommended_items = recommend_items(user_id, interaction_matrix, knn)
print(f"Recommended items for user {user_id}: {recommended_items}")
