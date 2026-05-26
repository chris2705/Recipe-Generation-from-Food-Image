# import the necessary libraries

import matplotlib.pyplot as plt
import torch
import torch.nn as nn
import numpy as np
import os
from Foodimg2Ing.args import get_parser
import pickle
from Foodimg2Ing.model import get_model
from torchvision import transforms
from Foodimg2Ing.utils.output_utils import prepare_output
from PIL import Image
import time
from Foodimg2Ing import app


def output(uploadedfile):

    # Keep all the codes and pre-trained weights in data directory
    data_dir=os.path.join(app.root_path,'data')


    # code will run in gpu if available and if the flag is set to True, else it will run on cpu
    use_gpu = True
    device = torch.device('cuda' if torch.cuda.is_available() and use_gpu else 'cpu')
    map_loc = None if torch.cuda.is_available() and use_gpu else 'cpu'



    # code below was used to save vocab files so that they can be loaded without Vocabulary class
    ingrs_vocab = pickle.load(open(os.path.join(data_dir, 'ingr_vocab.pkl'), 'rb'))
    vocab = pickle.load(open(os.path.join(data_dir, 'instr_vocab.pkl'), 'rb'))

    ingr_vocab_size = len(ingrs_vocab)
    instrs_vocab_size = len(vocab)
    output_dim = instrs_vocab_size

    

    t = time.time()
    import sys; sys.argv=['']; del sys
    args = get_parser()
    args.maxseqlen = 15
    args.ingrs_only=False
    model=get_model(args, ingr_vocab_size, instrs_vocab_size)
   
    # Load the pre-trained model parameters
    model_path = os.path.join(data_dir, 'modelbest.ckpt')
    model.load_state_dict(torch.load(model_path, map_location=map_loc))
    model.to(device)
    model.eval()
    model.ingrs_only = False
    model.recipe_only = False
   


    transf_list_batch = []
    transf_list_batch.append(transforms.ToTensor())
    transf_list_batch.append(transforms.Normalize((0.485, 0.456, 0.406), 
                                                (0.229, 0.224, 0.225)))
    to_input_transf = transforms.Compose(transf_list_batch)

    greedy = [True, False]
    beam = [-1, -1]
    temperature = 1.0
    numgens = len(greedy)

    uploaded_file=uploadedfile

    img=Image.open(uploaded_file).convert('RGB')
    
    show_anyways = False #if True, it will show the recipe even if it's not valid
    transf_list = []
    transf_list.append(transforms.Resize(256))
    transf_list.append(transforms.CenterCrop(224))
    transform = transforms.Compose(transf_list)
    
    image_transf = transform(img)
    image_tensor = to_input_transf(image_transf).unsqueeze(0).to(device)

    num_valid = 1
    title=[]
    ingredients=[]
    recipe=[]
    confidence_scores = []
    validity_info = []
    for i in range(numgens):
        with torch.no_grad():
            outputs = model.sample(image_tensor, greedy=greedy[i], 
                                temperature=temperature, beam=beam[i], true_ingrs=None)
                
        ingr_ids = outputs['ingr_ids'].cpu().numpy()
        recipe_ids = outputs['recipe_ids'].cpu().numpy()
                
        outs, valid = prepare_output(recipe_ids[0], ingr_ids[0], ingrs_vocab, vocab)

        # --- Confidence Scoring ---
        # Extract real probabilities from model outputs
        ingr_probs_tensor = outputs.get('ingr_probs')
        recipe_probs_tensor = outputs.get('recipe_probs')

        # 1. Ingredient probability: mean of max softmax values across predicted tokens
        ingr_confidence = 0.5  # default if unavailable
        if ingr_probs_tensor is not None:
            try:
                ingr_softmax = torch.nn.functional.softmax(ingr_probs_tensor, dim=-1)
                max_probs, _ = ingr_softmax.max(dim=-1)  # max probability at each position
                # Only consider non-padding positions
                mask = (torch.tensor(ingr_ids) != (len(ingrs_vocab) - 1)).float()
                if mask.sum() > 0:
                    ingr_confidence = float((max_probs.cpu().squeeze() * mask).sum() / mask.sum())
                ingr_confidence = min(max(ingr_confidence, 0.0), 1.0)
            except Exception:
                ingr_confidence = 0.5

        # 2. Diversity score from prepare_output (already computed)
        diversity_score = valid.get('score', 0.5)

        # 3. Validity bonus
        validity_bonus = 1.0 if valid['is_valid'] else 0.0

        # Combined confidence: weighted average
        confidence = (0.5 * ingr_confidence) + (0.3 * diversity_score) + (0.2 * validity_bonus)
        confidence = round(min(max(confidence, 0.0), 1.0), 4)

        confidence_scores.append(confidence)
        validity_info.append(valid)
        # --- End Confidence Scoring ---
            
        if valid['is_valid'] or show_anyways:
                
            title.append(outs['title'])

            ingredients.append(outs['ingrs'])

            recipe.append(outs['recipe'])
            

        else:
            title.append("Not a valid recipe!")
            recipe.append("Reason: "+valid['reason'])
            ingredients.append([])

    # Return the best confidence (from the greedy generation, index 0)
    best_confidence = confidence_scores[0] if confidence_scores else 0.5
    metadata = {
        'confidence_scores': confidence_scores,
        'validity_info': validity_info,
    }
            
    return title, ingredients, recipe, best_confidence, metadata

