import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def download_nq_data():
    """
    Download 1-minute NQ-equivalent data using QQQ as a proxy.
    QQQ tracks the NASDAQ-100 index which closely mirrors NQ futures movement.
    """
    print("Downloading 1-minute QQQ data (NQ proxy) for the past year...")
    
    # Calculate date range
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    try:
        # Download QQQ 1-minute data (max 30 days for free)
        # We'll download in chunks to get more data
        all_data = []
        
        # Download in 30-day chunks
        current_date = start_date
        chunk_size = timedelta(days=30)
        
        while current_date < end_date:
            chunk_end = min(current_date + chunk_size, end_date)
            
            print(f"Downloading data from {current_date.date()} to {chunk_end.date()}...")
            
            try:
                # Download QQQ data for this chunk
                ticker = yf.Ticker("QQQ")
                data = ticker.history(
                    start=current_date.strftime("%Y-%m-%d"),
                    end=chunk_end.strftime("%Y-%m-%d"),
                    interval="1m"
                )
                
                if not data.empty:
                    # Reset index to get timestamp as column
                    data = data.reset_index()
                    data['timestamp'] = data['Datetime']
                    
                    # Rename columns to match expected format
                    data = data.rename(columns={
                        'Open': 'open',
                        'High': 'high', 
                        'Low': 'low',
                        'Close': 'close',
                        'Volume': 'volume'
                    })
                    
                    # Keep only the columns we need
                    data = data[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                    
                    all_data.append(data)
                    print(f"  Retrieved {len(data)} bars")
                else:
                    print(f"  No data available for this period")
                    
            except Exception as e:
                print(f"  Error downloading chunk: {e}")
            
            current_date = chunk_end
        
        if all_data:
            # Combine all chunks
            combined_data = pd.concat(all_data, ignore_index=True)
            
            # Sort by timestamp
            combined_data = combined_data.sort_values('timestamp')
            
            # Remove duplicates
            combined_data = combined_data.drop_duplicates(subset=['timestamp'])
            
            # Save to CSV
            filename = f"qqq_1min_proxy_nq_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.csv"
            combined_data.to_csv(filename, index=False)
            
            print(f"✅ Successfully downloaded {len(combined_data)} total bars")
            print(f"✅ Saved to: {filename}")
            print(f"📊 Date range: {combined_data['timestamp'].min()} to {combined_data['timestamp'].max()}")
            
            return filename
        else:
            print("❌ No data was successfully downloaded")
            return None
            
    except Exception as e:
        print(f"❌ Error downloading data: {e}")
        return None

if __name__ == "__main__":
    # Install required package
    import subprocess
    import sys
    
    try:
        import yfinance
    except ImportError:
        print("Installing yfinance package...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "yfinance"])
        import yfinance
    
    download_nq_data()
