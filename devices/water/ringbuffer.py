class RingBuffer:
    """
    Simple implementation of a ring buffer.
    """
    def __init__(self, size):
        self.size = size
        self.data = [0 for i in range(self.size)]
        self.position = 0

  
    def append(self, x):
        """
        Append an element at the end of the buffer.
        """
        self.data[self.position] = x
        self.position = (self.position + 1) % self.size

    @property
    def count(self):
        """
        Return the count of items in the ring buffer.
        """
        return len(self.data)
        
    @property
    def avg(self):
        """
        Return the average of items in the ring buffer.
        """
        return sum(self.data) / self.size
        
    @property
    def sum(self):
        """
        Return the sum of items in the ring buffer.
        """
        return sum(self.data)
        
    @property
    def std_dev(self):
        """
        Return the standard deviation of items in the ring buffer.
        """
        n = 0
        mean = 0
        M2 = 0

        for x in self.data:
            n += 1
            delta = x - mean
            mean += delta / n
            M2 += delta * (x - mean)

        if n == 0:
            return 0  
            
        variance = M2 / n
        return variance ** 0.5